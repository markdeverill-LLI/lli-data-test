#!/usr/bin/env python3
"""
Lloyds List Intelligence API Token Provider

This utility retrieves a JWT token from the LLI tokenprovider API endpoint.
Credentials are read from LLI_CREDENTIALS.json and the token is stored in LLI_API_TOKEN.json

New: Supports --renew-if-daysold [days] which defaults to 29 when the flag is present without a value.
If the flag is omitted the script always requests a new token.
"""

import argparse
import base64
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

import requests


CREDENTIALS_FILE = "LLI_CREDENTIALS.json"
TOKEN_FILE = "LLI_API_TOKEN.json"


def load_credentials(credentials_file: str = CREDENTIALS_FILE) -> Dict[str, str]:
    cred_path = Path(credentials_file)
    if not cred_path.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {credentials_file}\n"
            "Create LLI_CREDENTIALS.json with the following format:\n"
            '{\n  "username": "YOUR_USERNAME",\n  "password": "YOUR_PASSWORD"\n}'
        )
    with open(cred_path, "r") as f:
        credentials = json.load(f)
    required_keys = {"username", "password"}
    missing_keys = required_keys - set(credentials.keys())
    if missing_keys:
        raise ValueError(
            f"Missing required credentials: {missing_keys}\nEnsure LLI_CREDENTIALS.json contains 'username' and 'password'"
        )
    return credentials


def get_token(username: str, password: str) -> str:
    url = "https://api.lloydslistintelligence.com/v1/tokenprovider"
    headers = {"Content-Type": "application/json"}
    payload = {"username": username, "password": password}

    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()

    response_data = response.json()

    # The API returns the JWT inside the 'Payload' field. Fall back to 'token' if present.
    if "Payload" in response_data:
        token = response_data["Payload"]
    elif "token" in response_data:
        token = response_data["token"]
    else:
        raise ValueError(f"Token not found in API response. Response keys: {list(response_data.keys())}")

    # If Payload is an object containing the token string under a different key, try common names
    if isinstance(token, dict):
        for key in ("token", "access_token", "jwt", "accessToken"):
            if key in token:
                return token[key]
        raise ValueError(f"Payload is an object but does not contain a known token field. Payload: {token}")

    if not isinstance(token, str):
        raise ValueError(f"Unexpected token type: {type(token)}. Value: {token}")

    return token


def save_token(token: str, token_file: str = TOKEN_FILE) -> None:
    token_data = {"token": token}
    with open(token_file, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"Token saved to {token_file}")


def load_saved_token(token_file: str = TOKEN_FILE) -> Optional[str]:
    path = Path(token_file)
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        token = data.get("token")
        if not token:
            return None
        return token
    except Exception:
        return None


def _base64url_decode(input_str: str) -> bytes:
    # Add padding if necessary
    rem = len(input_str) % 4
    if rem:
        input_str += "=" * (4 - rem)
    return base64.urlsafe_b64decode(input_str.encode("utf-8"))


def parse_jwt_payload(token: str) -> Dict[str, Any]:
    # JWT format: header.payload.signature
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Invalid JWT format")
    payload_b64 = parts[1]
    payload_bytes = _base64url_decode(payload_b64)
    payload = json.loads(payload_bytes.decode("utf-8"))
    return payload


def token_auth_time(token: str) -> Optional[datetime]:
    try:
        payload = parse_jwt_payload(token)
    except Exception:
        return None
    # auth_time is typically a numeric timestamp (seconds since epoch)
    auth = payload.get("auth_time")
    if auth is None:
        # try other common fields
        auth = payload.get("iat")
    if auth is None:
        return None
    try:
        auth_int = int(auth)
        return datetime.fromtimestamp(auth_int, tz=timezone.utc)
    except Exception:
        return None


def token_is_recent(token: str, days_old: int) -> bool:
    auth_dt = token_auth_time(token)
    if auth_dt is None:
        return False
    now = datetime.now(timezone.utc)
    age = now - auth_dt
    return age < timedelta(days=days_old)


def main() -> int:
    parser = argparse.ArgumentParser(description="Get or renew LLI API token")
    parser.add_argument(
        "--renew-if-daysold",
        nargs="?",
        const=29,
        type=int,
        default=None,
        help="Only renew the token if saved token is older than DAYS (default 29 when flag present without value). If flag omitted, always renew",
    )
    parser.add_argument(
        "--credentials",
        metavar="credentials-file",
        default=CREDENTIALS_FILE,
        help=f"Path to credentials JSON file (default: {CREDENTIALS_FILE})",
    )
    args = parser.parse_args()

    try:
        credentials = load_credentials(args.credentials)
    except Exception as e:
        print(f"Error loading credentials: {e}", file=sys.stderr)
        return 1

    should_call_api = True
    if args.renew_if_daysold is not None:
        days = args.renew_if_daysold
        saved = load_saved_token()
        if saved:
            try:
                if token_is_recent(saved, days):
                    auth_dt = token_auth_time(saved)
                    if auth_dt:
                        print(f"Existing token auth_time: {auth_dt.isoformat()} (within {days} days). Not renewing.")
                    else:
                        print(f"Existing token has no auth_time but appears recent; Not renewing.")
                    should_call_api = False
                else:
                    print(f"Existing token is older than {days} days; renewing.")
                    should_call_api = True
            except Exception as e:
                print(f"Failed to inspect saved token: {e}; will renew.")
                should_call_api = True
        else:
            print("No saved token found; will request a new one.")
            should_call_api = True
    else:
        # Flag not provided: always renew
        print("--renew-if-daysold not provided; requesting a fresh token.")
        should_call_api = True

    if not should_call_api:
        print("✓ Using existing token; no action needed.")
        return 0

    # Request a new token and save it
    try:
        print("Requesting token from LLI API...")
        token = get_token(credentials["username"], credentials["password"])
        save_token(token)
        print("✓ Token retrieved and saved successfully")
        return 0
    except requests.RequestException as e:
        print(f"API Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
