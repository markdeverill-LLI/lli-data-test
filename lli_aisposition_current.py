#!/usr/bin/env python3
"""
Lloyds List Intelligence AIS Position Current API

Retrieves current AIS position data from the LLI aispositioncurrent_v3 API endpoint.
Optionally fetches vessel basic characteristics data and creates a combined dataset.
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


def fetch_ais_data(token: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetch AIS position data from the API.
    
    Args:
        token: JWT authentication token
        params: Query parameters
        
    Returns:
        API response JSON
        
    Raises:
        requests.RequestException: If API call fails
    """
    url = "https://api.lloydslistintelligence.com/v1/aispositioncurrent_v3"
    headers = {
        "Authorization": token
    }
    
    # Construct and print the full URL for debugging
    from urllib.parse import urlencode
    query_string = urlencode(params)
    full_url = f"{url}?{query_string}"
    print(f"API Call: {full_url}")
    print(f"Authorization header: {token[:50]}..." if len(token) > 50 else f"Authorization header: {token}")
    
    response = requests.get(url, params=params, headers=headers)
    
    print(f"Response Status Code: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    
    response.raise_for_status()
    
    # Parse and log the response
    response_json = response.json()
    print(f"Response JSON (first 500 chars): {str(response_json)[:500]}")
    print(f"Response Keys: {list(response_json.keys()) if isinstance(response_json, dict) else type(response_json)}")
    
    return response_json


def fetch_vessel_basic_characteristics(token: str, vessel_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch vessel basic characteristics for a list of vessel IDs.
    
    Args:
        token: JWT authentication token
        vessel_ids: List of vessel ID strings
        
    Returns:
        Dictionary mapping vesselId to characteristics data
        
    Raises:
        requests.RequestException: If API call fails
    """
    url = "https://api.lloydslistintelligence.com/v1/vesselbasiccharacteristics"
    headers = {
        "Authorization": token
    }
    
    # Join vessel IDs with comma
    vessel_id_param = ",".join(vessel_ids)
    params = {
        "vesselId": vessel_id_param,
        "classb": "true",
        "aisClassIndicator": "true"
    }
    
    from urllib.parse import urlencode
    query_string = urlencode(params)
    full_url = f"{url}?{query_string}"
    print(f"API Call (Basic Characteristics): {full_url}")
    
    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    
    response_json = response.json()
    print(f"Basic Characteristics Response Status: {response.status_code}")
    print(f"Response Keys: {list(response_json.keys())}")
    
    # Parse response and create mapping
    characteristics_map = {}
    
    # Check for success flag
    if "IsSuccess" in response_json and response_json["IsSuccess"] is False:
        print(f"WARNING: API returned IsSuccess=false. Message: {response_json.get('Message', 'Unknown')}")
        return characteristics_map
    
    if "Data" in response_json:
        data = response_json["Data"]
        # The API returns "vessels" (lowercase), not "Vessels"
        vessels = data.get("vessels", []) if isinstance(data, dict) else data
        print(f"Found {len(vessels)} vessels in response")
        for vessel in vessels:
            if isinstance(vessel, dict):
                # Use "vesselId" (camelCase) key
                vessel_id = vessel.get("vesselId")
                if vessel_id:
                    characteristics_map[vessel_id] = vessel
    else:
        print(f"WARNING: No 'Data' key in response. Keys: {list(response_json.keys())}")
    
    print(f"Mapped {len(characteristics_map)} vessels")
    return characteristics_map


def merge_records(ais_positions: List[Dict[str, Any]], 
                  characteristics_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge AIS position data with vessel basic characteristics, avoiding duplicate columns.
    
    Args:
        ais_positions: List of AIS position records
        characteristics_map: Mapping of vesselId to characteristics data
        
    Returns:
        List of merged records
    """
    merged = []
    ais_keys = set(ais_positions[0].keys()) if ais_positions else set()
    
    for ais_record in ais_positions:
        merged_record = dict(ais_record)
        vessel_id = ais_record.get("VesselId")
        
        if vessel_id and vessel_id in characteristics_map:
            chars = characteristics_map[vessel_id]
            # Add only new keys from characteristics (not already in AIS data)
            for key, value in chars.items():
                if key not in ais_keys:
                    merged_record[key] = value
        
        merged.append(merged_record)
    
    return merged

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
    Save AIS data to CSV file with columns in the order they appear in the JSON.
    
    Args:
        data: List of AIS position records
        csv_path: Path to output CSV file
    """
    if not data:
        print(f"No data to save to CSV")
        return
    
    # Flatten records
    flattened = [flatten_record(record) for record in data]
    
    # Preserve field order from the JSON by using the first record's order
    # and collecting any additional fields from other records
    fieldnames = []
    seen = set()
    
    for record in flattened:
        for key in record.keys():
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flattened)
    
    print(f"CSV saved to {csv_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch current AIS position data from LLI API")
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
    parser.add_argument(
        "--addbasicchars",
        action="store_true",
        help="Fetch and merge vessel basic characteristics data for all vessels"
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
        
        # Fetch AIS data
        print("Fetching AIS position data...")
        response = fetch_ais_data(token, params)
        
        # Extract position data from response
        if not isinstance(response, dict):
            raise ValueError(f"API response is not a dict. Type: {type(response)}, Value: {response}")
        
        # Check for success flag
        is_success = response.get("IsSuccess")
        if is_success is False:
            error_msg = response.get("Message", "Unknown error")
            raise ValueError(f"API returned IsSuccess=false. Message: {error_msg}")
        
        if "Data" not in response:
            print(f"WARNING: 'Data' key not found in response. Available keys: {list(response.keys())}")
            print(f"Full response: {json.dumps(response, indent=2)}")
            raise ValueError(f"Unexpected API response structure. Expected 'Data' key.")
        
        data = response["Data"]
        
        # Extract AisPositions array
        positions = data.get("AisPositions", [])
        total_records = data.get("TotalRecords", len(positions))
        
        if not positions:
            print(f"WARNING: No AIS positions returned. Response Data keys: {list(data.keys())}")
            print(f"Full Data: {json.dumps(data, indent=2)[:1000]}")
        
        print(f"Total positions retrieved: {len(positions)} (API reports: {total_records})")
        
        # Determine output filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.outputfilename:
            # Use provided base name (strip any path and use stem to remove extension)
            base = Path(args.outputfilename).stem
            json_filename = f"{base}.json"
            csv_filename = f"{base}.csv"
        else:
            json_filename = f"aisposition_current_{timestamp}.json"
            csv_filename = f"aisposition_current_{timestamp}.csv"

        json_path = output_dir / json_filename
        
        # Save to JSON
        print(f"Saving JSON to {json_path}...")
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "parameters": params,
            "total_records": total_records,
            "data": positions
        }
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2)
        print(f"JSON saved to {json_path}")
        
        # Handle --addbasicchars option
        final_positions = positions
        if args.addbasicchars:
            print("\nFetching vessel basic characteristics...")
            # Extract unique vessel IDs
            vessel_ids = list(set(pos.get("VesselId") for pos in positions if pos.get("VesselId")))
            print(f"Fetching characteristics for {len(vessel_ids)} unique vessels...")
            
            # Fetch basic characteristics
            characteristics_map = fetch_vessel_basic_characteristics(token, vessel_ids)
            print(f"Retrieved characteristics for {len(characteristics_map)} vessels")
            
            # Save basic characteristics to separate JSON
            if args.outputfilename:
                base = Path(args.outputfilename).stem
                chars_json_filename = f"{base}_basicchars.json"
                chars_csv_filename = f"{base}_basicchars.csv"
            else:
                chars_json_filename = f"aisposition_current_{timestamp}_basicchars.json"
                chars_csv_filename = f"aisposition_current_{timestamp}_basicchars.csv"
            
            chars_json_path = output_dir / chars_json_filename
            chars_output_data = {
                "timestamp": datetime.now().isoformat(),
                "parameters": params,
                "total_records": len(characteristics_map),
                "data": list(characteristics_map.values())
            }
            
            with open(chars_json_path, 'w', encoding='utf-8') as f:
                json.dump(chars_output_data, f, indent=2)
            print(f"Basic characteristics JSON saved to {chars_json_path}")

            # Save basic characteristics CSV if requested
            if args.gencsv.lower() == "y":
                chars_csv_path = output_dir / chars_csv_filename
                print(f"Saving basic characteristics CSV to {chars_csv_path}...")
                save_to_csv(list(characteristics_map.values()), chars_csv_path)
            
            # Merge the data
            final_positions = merge_records(positions, characteristics_map)
            
            # Update CSV filename for combined data
            if args.outputfilename:
                base = Path(args.outputfilename).stem
                combined_csv_filename = f"{base}_combined.csv"
            else:
                combined_csv_filename = f"aisposition_current_{timestamp}_combined.csv"
        
        # Save to CSV if requested
        if args.gencsv.lower() == "y":
            csv_path = output_dir / csv_filename
            save_to_csv(positions, csv_path)
            
            # If addbasicchars, also save combined CSV
            if args.addbasicchars:
                combined_csv_path = output_dir / combined_csv_filename
                print(f"\nSaving combined CSV to {combined_csv_path}...")
                save_to_csv(final_positions, combined_csv_path)
        
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
