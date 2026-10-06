#!/usr/bin/env python3
"""
Oracle SQL Runner

A drop-in replacement for the Oracle "sqlplus" command-line tool on the SCCM
server. Runs a SQL script (DDL/DML, PL/SQL blocks, and SELECT statements),
spools SELECT output to CSV (honouring SET HEADING/COLSEP/FEEDBACK and SPOOL
commands embedded in the script, like real sqlplus), and writes a run log.

Does not require an Oracle Client install: it uses python-oracledb in "thin"
mode by default, connecting directly via host/port/service name.

Example:
    python oracle_sql_runner.py \\
        --sqlfile extract.sql \\
        --outputfile extract.csv \\
        --logfile extract.log \\
        --server dbhost.example.com --port 1521 --service ORCLPDB1 \\
        --oracleuser etl_user --password-env ORACLE_PASSWORD
"""

import argparse
import csv
import datetime
import getpass
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import oracledb
except ImportError:  # pragma: no cover - surfaced as a clear runtime error
    oracledb = None

LOG = logging.getLogger("oracle_sql_runner")

# sqlplus-style command keywords handled directly by this runner rather than
# sent to the database.
COMMAND_WORDS = {
    "SET", "SPOOL", "PROMPT", "WHENEVER", "DEFINE", "UNDEFINE", "EXIT", "QUIT",
    "COLUMN", "TTITLE", "BTITLE", "HOST", "CONNECT", "DISCONNECT", "CLEAR",
    "PAUSE", "SHOW", "ACCEPT", "REM", "REMARK",
}

SELECT_RE = re.compile(r"^(SELECT|WITH)\b", re.IGNORECASE)
PLSQL_START_RE = re.compile(
    r"^(BEGIN|DECLARE|CREATE\s+(OR\s+REPLACE\s+)?(PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b)",
    re.IGNORECASE,
)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


class ScriptAbort(Exception):
    """Raised to stop script processing early (EXIT command or fail-fast)."""

    def __init__(self, exit_code: int):
        super().__init__(f"Script aborted with exit code {exit_code}")
        self.exit_code = exit_code


class ScriptState:
    """Tracks sqlplus SET/SPOOL/WHENEVER state while the script runs."""

    def __init__(self, default_writer=None, default_label: Optional[str] = None, default_file=None):
        self.heading = True
        self.feedback = True
        self.colsep = ","
        self.whenever_mode = "CONTINUE"  # or "EXIT"
        self.whenever_code = 1
        self.default_writer = default_writer
        self.default_label = default_label
        self.default_file = default_file
        self.inline_file = None
        self.inline_writer = None
        self.inline_label = None

    @property
    def active_writer(self):
        return self.inline_writer or self.default_writer

    @property
    def active_label(self):
        return self.inline_label or self.default_label

    def open_inline_spool(self, filename: str, encoding: str):
        self.close_inline_spool()
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".csv")
        fh = open(path, "w", newline="", encoding=encoding)
        self.inline_file = fh
        self.inline_writer = csv.writer(fh, delimiter=self.colsep if len(self.colsep) == 1 else ",")
        self.inline_label = str(path)
        LOG.info("SPOOL %s", path)

    def close_inline_spool(self):
        if self.inline_file:
            self.inline_file.close()
            LOG.info("SPOOL OFF (%s)", self.inline_label)
        self.inline_file = None
        self.inline_writer = None
        self.inline_label = None

    def close_all(self):
        self.close_inline_spool()
        if self.default_file:
            self.default_file.close()


def strip_comments(sql_text: str) -> str:
    """Remove /* ... */ block comments and full-line REM/-- comments."""
    sql_text = BLOCK_COMMENT_RE.sub("", sql_text)
    cleaned_lines = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("REM") and (len(stripped) == 3 or stripped[3] in " \t"):
            continue
        if stripped.startswith("--"):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def substitute_variables(sql_text: str, variables: Dict[str, str], positional: List[str]) -> str:
    """Replace sqlplus &NAME / &&NAME and &1 &2 ... substitution variables."""

    def repl(match: "re.Match") -> str:
        name = match.group("name")
        if name.isdigit():
            idx = int(name) - 1
            if 0 <= idx < len(positional):
                return positional[idx]
            return match.group(0)
        if name in variables:
            return variables[name]
        if name.upper() in variables:
            return variables[name.upper()]
        return match.group(0)

    pattern = re.compile(r"&&?(?P<name>[A-Za-z_][A-Za-z0-9_]*|[0-9]+)")
    return pattern.sub(repl, sql_text)


def tokenize(sql_text: str):
    """Yield ('command', word, rest) or ('sql', statement_text, is_plsql) tuples."""
    buffer: List[str] = []
    is_plsql = False
    buffering = False

    for raw_line in sql_text.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()

        if not buffering:
            if stripped == "":
                continue
            first_word = re.split(r"[\s;]", stripped, maxsplit=1)[0].upper()
            if first_word in COMMAND_WORDS:
                rest = stripped[len(first_word):].strip()
                yield ("command", first_word, rest)
                continue
            if stripped == "/":
                # Stray terminator with nothing buffered; ignore.
                continue
            # Start buffering a new SQL/PL-SQL statement.
            buffer = [line]
            is_plsql = bool(PLSQL_START_RE.match(stripped))
            buffering = True
            if not is_plsql and stripped.endswith(";"):
                statement_text = _strip_trailing_semicolon("\n".join(buffer))
                yield ("sql", statement_text, is_plsql)
                buffer = []
                buffering = False
            continue

        # Currently buffering a multi-line statement.
        if stripped == "/":
            statement_text = "\n".join(buffer)
            yield ("sql", statement_text, is_plsql)
            buffer = []
            buffering = False
            continue

        buffer.append(line)
        if not is_plsql and stripped.endswith(";"):
            statement_text = _strip_trailing_semicolon("\n".join(buffer))
            yield ("sql", statement_text, is_plsql)
            buffer = []
            buffering = False

    if buffer and "\n".join(buffer).strip():
        # Trailing statement without an explicit terminator (common for the
        # last line of a file). Execute it as-is.
        yield ("sql", _strip_trailing_semicolon("\n".join(buffer)), is_plsql)


def _strip_trailing_semicolon(text: str) -> str:
    text = text.rstrip()
    if text.endswith(";"):
        text = text[:-1]
    return text


def format_cell(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "read") and not isinstance(value, (str, bytes)):
        try:
            value = value.read()
        except Exception:  # pragma: no cover - defensive
            pass
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def handle_command(word: str, rest: str, state: ScriptState, args) -> Optional[int]:
    """Handle a sqlplus directive. Returns an exit code if script should stop."""
    if word in ("REM", "REMARK"):
        return None

    if word == "PROMPT":
        LOG.info("%s", rest)
        return None

    if word == "SPOOL":
        target = rest.strip().strip("'\"")
        if target.upper() in ("OFF", "OUT", ""):
            state.close_inline_spool()
        else:
            state.open_inline_spool(target, args.encoding)
        return None

    if word == "SET":
        parts = rest.split()
        if len(parts) >= 2:
            key = parts[0].upper()
            val = parts[1].upper()
            if key in ("HEADING", "HEAD"):
                state.heading = val in ("ON", "TRUE")
            elif key in ("FEEDBACK", "FEED"):
                state.feedback = val not in ("OFF", "FALSE", "0")
            elif key == "COLSEP":
                state.colsep = parts[1].strip("'\"")
            else:
                LOG.debug("Ignoring SET %s %s (not applicable to CSV output)", key, val)
        return None

    if word == "WHENEVER":
        parts = rest.split()
        if len(parts) >= 2 and parts[0].upper() == "SQLERROR":
            action = parts[1].upper()
            if action == "EXIT":
                code = 1
                if len(parts) >= 3 and parts[2].isdigit():
                    code = int(parts[2])
                state.whenever_mode = "EXIT"
                state.whenever_code = code
            elif action == "CONTINUE":
                state.whenever_mode = "CONTINUE"
        return None

    if word == "DEFINE":
        LOG.debug(
            "Ignoring in-script DEFINE %r; pass substitution values with --var instead.",
            rest,
        )
        return None

    if word in ("EXIT", "QUIT"):
        code = 0
        token = rest.split()[0].upper() if rest.split() else ""
        if token.isdigit():
            code = int(token)
        elif token in ("FAILURE",):
            code = 1
        raise ScriptAbort(code)

    # COLUMN, TTITLE, BTITLE, HOST, CONNECT, DISCONNECT, CLEAR, PAUSE, SHOW,
    # ACCEPT, UNDEFINE: not meaningful for unattended CSV extraction.
    LOG.debug("Ignoring sqlplus command: %s %s", word, rest)
    return None


def execute_statement(cursor, conn, text: str, is_plsql: bool, state: ScriptState) -> None:
    stripped = text.strip()
    if not stripped:
        return

    LOG.info("Executing: %s", _summarize(stripped))

    if not is_plsql and SELECT_RE.match(stripped):
        cursor.execute(stripped)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        if state.feedback:
            LOG.info("%d rows selected.", len(rows))
        writer = state.active_writer
        if writer is not None:
            if state.heading:
                writer.writerow(columns)
            for row in rows:
                writer.writerow([format_cell(v) for v in row])
        else:
            LOG.warning(
                "SELECT returned %d rows but no --outputfile or SPOOL target is "
                "active; output discarded.",
                len(rows),
            )
        return

    cursor.execute(stripped)
    rowcount = cursor.rowcount
    conn.commit()
    if state.feedback and rowcount and rowcount > 0:
        LOG.info("%d rows affected.", rowcount)
    else:
        LOG.info("Statement completed.")


def _summarize(stmt: str, max_len: int = 120) -> str:
    one_line = " ".join(stmt.split())
    return one_line if len(one_line) <= max_len else one_line[: max_len - 3] + "..."


def build_dsn(args) -> str:
    if args.dsn:
        return args.dsn
    if args.server and args.service:
        return f"{args.server}:{args.port}/{args.service}"
    if args.service:
        # No host/port given: rely on tnsnames.ora resolution (requires thick
        # mode / Oracle Client with TNS_ADMIN configured).
        return args.service
    raise SystemExit(
        "Must supply --dsn, or --service with --server (and optional --port), "
        "or --service alone with --thick-mode and TNS_ADMIN configured."
    )


def get_password(args) -> str:
    if args.password:
        return args.password
    env_val = os.environ.get(args.password_env)
    if env_val:
        return env_val
    if sys.stdin.isatty():
        return getpass.getpass(f"Oracle password for {args.oracleuser}: ")
    raise SystemExit(
        f"No password supplied. Use --password, set the {args.password_env} "
        "environment variable, or run interactively."
    )


def setup_logging(logfile: Optional[str], verbose: bool) -> None:
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    LOG.addHandler(console)

    if logfile:
        file_handler = logging.FileHandler(logfile, mode="w", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        LOG.addHandler(file_handler)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an Oracle SQL script, spooling SELECT output to CSV "
        "(a sqlplus replacement for unattended/SCCM jobs).",
    )
    parser.add_argument("--sqlfile", required=True, help="Path to the SQL script to execute.")
    parser.add_argument(
        "--outputfile",
        help="CSV file to spool SELECT output to, used when the script has no "
        "inline SPOOL command (or as the target to return to after SPOOL OFF).",
    )
    parser.add_argument("--logfile", help="Path to write a run log to. Always logs to stdout as well.")

    parser.add_argument("--service", help="Oracle TNS service name (e.g. ORCLPDB1).")
    parser.add_argument("--server", help="Database host name or IP address.")
    parser.add_argument("--port", type=int, default=1521, help="Database listener port (default 1521).")
    parser.add_argument(
        "--dsn",
        help="Full connect string/Easy Connect override, takes precedence over "
        "--service/--server/--port.",
    )

    parser.add_argument("--oracleuser", required=True, help="Oracle username.")
    parser.add_argument("--password", help="Oracle password. Prefer --password-env for automation.")
    parser.add_argument(
        "--password-env",
        default="ORACLE_PASSWORD",
        help="Environment variable to read the password from if --password is "
        "not given (default: ORACLE_PASSWORD).",
    )

    parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Define a substitution variable for &NAME references in the script. Repeatable.",
    )
    parser.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="VALUE",
        help="Positional substitution value for &1, &2, ... references. Repeatable, in order.",
    )

    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Abort on the first SQL error, regardless of in-script WHENEVER "
        "SQLERROR directives (default: honour the script, or continue).",
    )
    parser.add_argument(
        "--thick-mode",
        action="store_true",
        help="Initialize the Oracle Client (thick mode) instead of the default "
        "pure-Python thin mode. Needed for tnsnames.ora/TNS alias resolution.",
    )
    parser.add_argument("--oracle-client-lib-dir", help="Oracle Instant Client directory for --thick-mode.")
    parser.add_argument("--encoding", default="utf-8", help="Encoding for output/log files (default utf-8).")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging to the console.")

    return parser.parse_args(argv)


def parse_var_list(pairs: List[str]) -> Dict[str, str]:
    variables = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--var must be in NAME=VALUE form, got: {pair!r}")
        name, value = pair.split("=", 1)
        variables[name.strip()] = value
        variables[name.strip().upper()] = value
    return variables


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.logfile, args.verbose)

    if oracledb is None:
        LOG.error("The 'oracledb' package is required. Install it with: pip install oracledb")
        return 1

    sql_path = Path(args.sqlfile)
    if not sql_path.exists():
        LOG.error("SQL file not found: %s", sql_path)
        return 1

    raw_text = sql_path.read_text(encoding=args.encoding, errors="replace")
    raw_text = raw_text.lstrip("\ufeff")  # strip BOM if present
    variables = parse_var_list(args.var)
    text = substitute_variables(raw_text, variables, args.arg)
    text = strip_comments(text)

    statement_count = sum(1 for kind, _a, _b in tokenize(text) if kind == "sql")
    if not args.outputfile:
        if statement_count == 0:
            LOG.error(
                "No --outputfile was specified, so %s must contain one or more "
                "SQL statements to execute, but none were found.",
                sql_path,
            )
            return 1
        LOG.info(
            "No --outputfile specified; running in execute-only mode (%d "
            "statement(s) to execute). Any SELECT output will be discarded "
            "unless the script uses its own SPOOL command.",
            statement_count,
        )

    if args.thick_mode:
        init_kwargs = {}
        if args.oracle_client_lib_dir:
            init_kwargs["lib_dir"] = args.oracle_client_lib_dir
        oracledb.init_oracle_client(**init_kwargs)

    dsn = build_dsn(args)
    password = get_password(args)

    default_fh = None
    default_writer = None
    if args.outputfile:
        default_fh = open(args.outputfile, "w", newline="", encoding=args.encoding)
        default_writer = csv.writer(default_fh)

    state = ScriptState(default_writer=default_writer, default_label=args.outputfile, default_file=default_fh)

    stmt_count = 0
    error_count = 0
    exit_code = 0

    try:
        LOG.info("Connecting to %s as %s", dsn, args.oracleuser)
        conn = oracledb.connect(user=args.oracleuser, password=password, dsn=dsn)
    except Exception as exc:
        LOG.error("Connection failed: %s", exc)
        if default_fh:
            default_fh.close()
        return 1

    try:
        cursor = conn.cursor()
        try:
            for kind, a, b in tokenize(text):
                if kind == "command":
                    word, rest = a, b
                    try:
                        handle_command(word, rest, state, args)
                    except ScriptAbort as abort:
                        exit_code = abort.exit_code
                        raise
                else:
                    stmt_text, is_plsql = a, b
                    stmt_count += 1
                    try:
                        execute_statement(cursor, conn, stmt_text, is_plsql, state)
                    except Exception as exc:
                        error_count += 1
                        LOG.error("Error executing statement: %s", exc)
                        LOG.debug("Failing statement was: %s", stmt_text)
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        if args.fail_fast or state.whenever_mode == "EXIT":
                            exit_code = state.whenever_code if not args.fail_fast else 1
                            raise ScriptAbort(exit_code)
        except ScriptAbort as abort:
            exit_code = abort.exit_code
        finally:
            cursor.close()
    finally:
        state.close_all()
        conn.close()

    if exit_code == 0 and error_count > 0:
        exit_code = 1

    LOG.info(
        "Done. Statements executed: %d, errors: %d, exit code: %d",
        stmt_count,
        error_count,
        exit_code,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
