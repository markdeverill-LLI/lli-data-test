#!/usr/bin/env python3
"""Emit shell commands that import an AWS Secrets Manager JSON secret."""

import argparse
import base64
import json
import re
import shlex
import sys
from typing import Dict


DEFAULT_SECRET_NAME = "prod/dataservices/sharedsecrets"
DEFAULT_PREFIX = "llids-"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Retrieve a JSON object from AWS Secrets Manager and emit commands "
            "that set each key as a process environment variable."
        )
    )
    parser.add_argument(
        "--secret-name",
        default=DEFAULT_SECRET_NAME,
        help=f"AWS secret name or ARN (default: {DEFAULT_SECRET_NAME})",
    )
    parser.add_argument("--region", help="AWS region; otherwise use AWS configuration")
    parser.add_argument("--profile", help="AWS CLI profile name")
    parser.add_argument(
        "--shell",
        choices=("powershell", "cmd", "bash"),
        default="powershell",
        help="Shell command format to emit (default: powershell)",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Required secret-key prefix (default: {DEFAULT_PREFIX})",
    )
    return parser.parse_args()


def get_secret_value(
    secret_name: str,
    region: str = None,
    profile: str = None,
) -> str:
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError(
            "boto3 is required. Install it with: python -m pip install boto3"
        ) from error

    session = boto3.Session(profile_name=profile, region_name=region)
    client = session.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)

    if "SecretString" in response:
        return response["SecretString"]
    if "SecretBinary" in response:
        secret_binary = response["SecretBinary"]
        if isinstance(secret_binary, str):
            secret_binary = base64.b64decode(secret_binary)
        return secret_binary.decode("utf-8")
    raise ValueError(f"AWS returned no secret value for {secret_name}")


def parse_secret(secret_value: str, prefix: str) -> Dict[str, str]:
    try:
        parsed = json.loads(secret_value)
    except json.JSONDecodeError as error:
        raise ValueError("The AWS secret value is not valid JSON") from error

    if not isinstance(parsed, dict):
        raise ValueError("The AWS secret must contain a JSON object of key/value pairs")
    if not parsed:
        raise ValueError("The AWS secret contains no key/value pairs")

    environment_values = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Every secret key must be a non-empty string")
        if prefix and not key.startswith(prefix):
            raise ValueError(
                f"Secret key {key!r} does not start with required prefix {prefix!r}"
            )
        if value is None or isinstance(value, (dict, list)):
            raise ValueError(f"Secret value for {key!r} must be a scalar value")

        string_value = str(value)
        if "\x00" in key or "\x00" in string_value:
            raise ValueError(f"Secret key {key!r} contains a null character")
        if "\r" in key or "\n" in key or "\r" in string_value or "\n" in string_value:
            raise ValueError(
                f"Secret key {key!r} contains a newline, which cannot be emitted safely"
            )
        environment_values[key] = string_value

    return environment_values


def quote_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def emit_powershell(environment_values: Dict[str, str]) -> None:
    for key, value in environment_values.items():
        print(
            "[Environment]::SetEnvironmentVariable("
            f"{quote_powershell(key)}, {quote_powershell(value)}, 'Process')"
        )


def emit_cmd(environment_values: Dict[str, str]) -> None:
    for key, value in environment_values.items():
        escaped_value = value.replace("%", "%%")
        print(f'set "{key}={escaped_value}"')


def normalize_bash_key(key: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", key).upper()
    if not normalized or normalized[0].isdigit():
        normalized = "_" + normalized
    return normalized


def emit_bash(environment_values: Dict[str, str]) -> None:
    normalized_keys = {}
    for key, value in environment_values.items():
        normalized_key = normalize_bash_key(key)
        original_key = normalized_keys.get(normalized_key)
        if original_key is not None:
            raise ValueError(
                f"Secret keys {original_key!r} and {key!r} both normalize "
                f"to Bash variable {normalized_key!r}"
            )
        normalized_keys[normalized_key] = key
        print(f"export {normalized_key}={shlex.quote(value)}")


def main() -> int:
    args = parse_args()
    try:
        secret_value = get_secret_value(
            secret_name=args.secret_name,
            region=args.region,
            profile=args.profile,
        )
        environment_values = parse_secret(secret_value, args.prefix)

        if args.shell == "powershell":
            emit_powershell(environment_values)
        elif args.shell == "cmd":
            emit_cmd(environment_values)
        else:
            emit_bash(environment_values)

        print(
            f"Retrieved {len(environment_values)} environment variable(s) "
            f"from {args.secret_name}.",
            file=sys.stderr,
        )
        return 0
    except Exception as error:
        print(f"Secret retrieval failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
