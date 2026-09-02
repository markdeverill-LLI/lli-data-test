#!/usr/bin/env python3
"""
Lloyds List Intelligence Vessel List API

Retrieves vessel list data from the LLI vessellist_v3 API endpoint with automatic pagination.
Results are saved to JSON and optionally converted to CSV.
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


TOKEN_FILE = "LLI_API_TOKEN.json"
DEFAULT_OUTPUT_DIR = "output"


def load_token(token_file: str = TOKEN_FILE) -> str:
    """
    Load JWT token from JSON file.
    
    Args:
        token_file: Path to the token JSON file
        
    Returns:
        JWT token string
        
    Raises:
        FileNotFoundError: If token file doesn't exist
        ValueError: If token not in file
    """
    path = Path(token_file)
    
    if not path.exists():
        raise FileNotFoundError(
            f"Token file not found: {token_file}\n"
            "Run lli_get_token.py first to generate a token."
        )
    
    with open(path, 'r') as f:
        data = json.load(f)
    
    token = data.get("token")
    if not token:
        raise ValueError(f"Token not found in {token_file}")
    
    return token


def parse_parameters(param_string: str) -> Dict[str, Any]:
    """
    Parse parameter string into a dictionary.
    
    Supports formats like:
      - key1=value1&key2=value2
      - key1=value1 key2=value2
      - {"key1": "value1", "key2": "value2"}
    
    Args:
        param_string: Parameter string
        
    Returns:
        Dictionary of parameters
    """
    params = {}
    
    # Try JSON format first
    if param_string.strip().startswith("{"):
        try:
            params = json.loads(param_string)
            return params
        except json.JSONDecodeError:
            pass
    
    # Try URL-encoded format (key=value&key2=value2)
    if "&" in param_string or "=" in param_string:
        for pair in param_string.split("&"):
            pair = pair.strip()
            if "=" in pair:
                key, val = pair.split("=", 1)
                # Try to parse as number or boolean
                try:
                    params[key.strip()] = int(val.strip())
                except ValueError:
                    if val.lower() in ("true", "false"):
                        params[key.strip()] = val.lower() == "true"
                    else:
                        params[key.strip()] = val.strip()
        return params
    
    # Try space-separated format (key1=value1 key2=value2)
    for pair in param_string.split():
        if "=" in pair:
            key, val = pair.split("=", 1)
            try:
                params[key.strip()] = int(val.strip())
            except ValueError:
                if val.lower() in ("true", "false"):
                    params[key.strip()] = val.lower() == "true"
                else:
                    params[key.strip()] = val.strip()
    
    return params


def fetch_vessel_data(token: str, params: Dict[str, Any], page: int = 1) -> Dict[str, Any]:
    """
    Fetch vessel data from the API for a specific page.
    
    Args:
        token: JWT authentication token
        params: Query parameters
        page: Page number
        
    Returns:
        API response JSON
        
    Raises:
        requests.RequestException: If API call fails
    """
    url = "https://api.lloydslistintelligence.com/v1/vessellist_v3"
    headers = {
        "Authorization": token
    }
    
    # Add page number to parameters
    request_params = params.copy()
    request_params["pageNumber"] = page
    
    response = requests.get(url, params=request_params, headers=headers)
    response.raise_for_status()
    
    return response.json()


def fetch_all_pages(token: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Fetch all pages of vessel data.
    
    Args:
        token: JWT authentication token
        params: Query parameters
        
    Returns:
        Consolidated list of vessel records from all pages
    """
    all_data = []
    page = 1
    total_pages = None
    
    while True:
        print(f"Fetching page {page}...", end=" ", flush=True)
        response = fetch_vessel_data(token, params, page)
        
        # Check if response has data
        if "Data" not in response:
            raise ValueError(f"Unexpected API response: {response}")
        
        data = response["Data"]
        
        # Get pagination info
        current_page = data.get("CurrentPage")
        total_pages = data.get("TotalPages")
        
        if current_page is None or total_pages is None:
            raise ValueError(f"Missing pagination info in response: {data}")
        
        print(f"{current_page}/{total_pages}", flush=True)
        
        # Extract vessel records
        vessels = data.get("Vessels", [])
        all_data.extend(vessels)
        
        # Check if we need to continue
        if current_page >= total_pages:
            break
        
        page += 1
    
    return all_data


def flatten_record(record: Dict[str, Any], parent_key: str = "") -> Dict[str, Any]:
    """
    Flatten nested dictionary for CSV export.
    
    Args:
        record: Dictionary to flatten
        parent_key: Parent key prefix for nested keys
        
    Returns:
        Flattened dictionary
    """
    items = []
    
    for key, value in record.items():
        new_key = f"{parent_key}_{key}" if parent_key else key
        
        if isinstance(value, dict):
            items.extend(flatten_record(value, new_key).items())
        elif isinstance(value, list):
            # Convert list to string representation
            items.append((new_key, json.dumps(value)))
        else:
            items.append((new_key, value))
    
    return dict(items)


def save_to_csv(data: List[Dict[str, Any]], csv_path: Path) -> None:
    """
    Save vessel data to CSV file.
    
    Args:
        data: List of vessel records
        csv_path: Path to output CSV file
    """
    if not data:
        print(f"No data to save to CSV")
        return
    
    # Flatten records
    flattened = [flatten_record(record) for record in data]
    
    # Get all unique keys across all records
    fieldnames = set()
    for record in flattened:
        fieldnames.update(record.keys())
    fieldnames = sorted(fieldnames)
    
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flattened)
    
    print(f"CSV saved to {csv_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch vessel list data from LLI API")
    parser.add_argument(
        "--parameters",
        required=True,
        help="API parameters (e.g., 'key1=value1&key2=value2' or JSON format)"
    )
    parser.add_argument(
        "--outputdir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--gencsv",
        choices=["y", "n"],
        default="y",
        help="Generate CSV file (default: y)"
    )
    parser.add_argument(
        "--outputfilename",
        metavar="filename",
        default=None,
        help="Base filename to use for both JSON and CSV outputs (no extension). If omitted, a timestamped name is used."
    )
    args = parser.parse_args()
    
    try:
        # Load token
        print("Loading authentication token...")
        token = load_token()
        
        # Parse parameters
        print("Parsing parameters...")
        params = parse_parameters(args.parameters)
        print(f"Parameters: {params}")
        
        # Create output directory
        output_dir = Path(args.outputdir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Fetch all pages
        print("Fetching vessel data...")
        all_vessels = fetch_all_pages(token, params)
        print(f"Total vessels retrieved: {len(all_vessels)}")
        
        # Determine output filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.outputfilename:
            # Use provided base name (strip any path and use stem to remove extension)
            base = Path(args.outputfilename).stem
            json_filename = f"{base}.json"
            csv_filename = f"{base}.csv"
        else:
            json_filename = f"vessel_list_{timestamp}.json"
            csv_filename = f"vessel_list_{timestamp}.csv"

        json_path = output_dir / json_filename
        
        # Save to JSON
        print(f"Saving JSON to {json_path}...")
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "parameters": params,
            "total_records": len(all_vessels),
            "data": all_vessels
        }
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2)
        print(f"JSON saved to {json_path}")
        
        # Save to CSV if requested
        if args.gencsv.lower() == "y":
            csv_path = output_dir / csv_filename
            save_to_csv(all_vessels, csv_path)
        
        print("✓ Completed successfully")
        return 0
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except requests.RequestException as e:
        print(f"API Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
