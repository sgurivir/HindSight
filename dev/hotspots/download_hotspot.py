#!/usr/bin/env python3
"""
Download Hotspot Data from SpinDistill API

Downloads hotspot JSON data for a specified daemon/process from Apple's SpinDistill API
and saves it to /tmp/ directory.

Usage:
    python dev/download_hotspot.py --daemon locationd --dataset "NapiliB_Seed_4_(23S5031a)" --device N210
    python dev/download_hotspot.py -d locationd -s "MyBuild_(1.0)" -v D83
    python dev/download_hotspot.py --daemon mediaserverd --dataset "Luck_Seed_5_(23A5308g)" --device D83

Requirements:
    - Apple Connect must be installed and configured for authentication
    - Network access to spindistill.apple.com
"""

import os
import sys
import json
import time
import argparse
import subprocess
from datetime import datetime
from urllib.parse import urlencode
from typing import Optional, Dict, Any

try:
    import requests
    import urllib3
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)

# Disable SSL warnings for internal Apple API
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# API Configuration Constants
BASE_URL = "https://spindistill.apple.com/api/v2/hotspots"
COOKIE_COMMAND = "appleconnect serviceTicket -I 200005 -d -n"
COOKIE_TTL_SECONDS = 3600
REQUEST_TIMEOUT_SECONDS = 30
JSON_INDENT_SPACES = 2
SSL_VERIFY = False  # Disable SSL verification for internal Apple API

# Default output directory
DEFAULT_OUTPUT_DIR = "/tmp"

# Default parameter values
DEFAULT_DATASET = "NapiliD_Seed_3_(23S5611c)"
DEFAULT_PROCESS = "locationd"
DEFAULT_DEVICE = "N210"
DEFAULT_CONTEXT_FILTER = "Unplugged"
DEFAULT_COUNTRY_CODE = "All"
DEFAULT_SLICE_TYPE = "Overall"


class HotspotDownloader:
    """
    A client for downloading hotspot data from Apple's SpinDistill API.
    """

    # Cookie caching
    _cached_cookie: Optional[str] = None
    _cookie_timestamp: Optional[float] = None

    def __init__(self,
                 daemon: str,
                 dataset: str,
                 device: str,
                 context_filter: str = DEFAULT_CONTEXT_FILTER,
                 country_code: str = DEFAULT_COUNTRY_CODE,
                 slice_type: str = DEFAULT_SLICE_TYPE,
                 output_dir: str = DEFAULT_OUTPUT_DIR,
                 ssl_verify: bool = SSL_VERIFY):
        """
        Initialize the HotspotDownloader.

        Args:
            daemon: Daemon/process name (e.g., locationd, mediaserverd)
            dataset: Dataset identifier (e.g., "NapiliB_Seed_4_(23S5031a)")
            device: Device identifier (e.g., N210, D83)
            context_filter: Context filter type (default: "Unplugged")
            country_code: Country code filter (default: "All")
            slice_type: Slice type filter (default: "Overall")
            output_dir: Output directory for saved files (default: /tmp)
            ssl_verify: Whether to verify SSL certificates (default: False)
        """
        self.daemon = daemon
        self.dataset = dataset
        self.device = device
        self.context_filter = context_filter
        self.country_code = country_code
        self.slice_type = slice_type
        self.output_dir = output_dir
        self.ssl_verify = ssl_verify

    @staticmethod
    def _execute_cookie_command() -> str:
        """Execute the Apple Connect cookie command and return the cookie value."""
        try:
            result = subprocess.run(
                COOKIE_COMMAND.split(),
                capture_output=True,
                text=True,
                check=True
            )
            cookie = result.stdout.strip()
            if not cookie:
                raise ValueError("Empty cookie received from Apple Connect command")
            return cookie
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to get authentication cookie. "
                f"Ensure Apple Connect is installed and configured.\n"
                f"Error: {e}"
            )
        except FileNotFoundError:
            raise RuntimeError(
                "Apple Connect command not found. "
                "Please install Apple Connect and ensure it's in your PATH."
            )
        except Exception as e:
            raise RuntimeError(f"Error executing cookie command: {e}")

    @classmethod
    def _get_cached_cookie(cls) -> str:
        """Get cached cookie or fetch a new one if expired."""
        current_time = time.time()

        # Check if we have a valid cached cookie
        if (cls._cached_cookie and
            cls._cookie_timestamp and
            (current_time - cls._cookie_timestamp) < COOKIE_TTL_SECONDS):
            return cls._cached_cookie

        # Fetch new cookie and cache it
        print("Authenticating with Apple Connect...")
        cls._cached_cookie = cls._execute_cookie_command()
        cls._cookie_timestamp = current_time
        return cls._cached_cookie

    def _ensure_output_directory(self) -> None:
        """Ensure the output directory exists."""
        os.makedirs(self.output_dir, exist_ok=True)

    def _save_json_data(self, data: Dict[str, Any], filename: str) -> str:
        """Save data to JSON file with timestamp."""
        self._ensure_output_directory()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        full_filename = f"{timestamp}_{filename}.json"
        filepath = os.path.join(self.output_dir, full_filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=JSON_INDENT_SPACES)

        return filepath

    def _make_authenticated_request(self, url: str, headers: Dict[str, str]) -> Dict[str, Any]:
        """Make HTTP request with error handling."""
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
                verify=self.ssl_verify
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Request timed out after {REQUEST_TIMEOUT_SECONDS} seconds")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Failed to connect to {BASE_URL}. "
                "Check your network connection and VPN status."
            )
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"HTTP error: {e}")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Request failed: {e}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse JSON response: {e}")

    def _build_data_url(self) -> str:
        """Build the data URL with configurable parameters."""
        params = {
            'dataSet': self.dataset,
            'process': self.daemon,
            'device': self.device,
            'contextFilter': self.context_filter,
            'countryCode': self.country_code,
            'slice': self.slice_type
        }

        return f"{BASE_URL}/data?{urlencode(params)}"

    def _get_authentication_headers(self) -> Dict[str, str]:
        """Get authentication headers with cookie."""
        cookie = self._get_cached_cookie()
        return {"Cookie": f"acack={cookie}"}

    def download(self) -> str:
        """
        Download hotspot data and save to file.

        Returns:
            Path to the saved JSON file
        """
        url = self._build_data_url()
        headers = self._get_authentication_headers()

        print(f"Downloading hotspot data...")
        print(f"  Daemon: {self.daemon}")
        print(f"  Dataset: {self.dataset}")
        print(f"  Device: {self.device}")
        print(f"  Context Filter: {self.context_filter}")

        data = self._make_authenticated_request(url, headers)

        # Create filename based on daemon name
        filename = f"{self.daemon}_hotspot_data"
        filepath = self._save_json_data(data, filename)

        # Print summary
        print(f"\n✅ Hotspot data downloaded successfully!")
        print(f"   File: {filepath}")
        print(f"   Process: {data.get('processName', 'N/A')}")
        print(f"   User count: {data.get('userCount', 'N/A')}")
        print(f"   Sample count: {data.get('sampleCount', 'N/A')}")

        return filepath

    def get_configuration(self) -> Dict[str, Any]:
        """Get current client configuration."""
        return {
            "daemon": self.daemon,
            "dataset": self.dataset,
            "device": self.device,
            "context_filter": self.context_filter,
            "country_code": self.country_code,
            "slice_type": self.slice_type,
            "output_dir": self.output_dir,
            "base_url": BASE_URL
        }


def main():
    """Main entry point for downloading hotspot data."""
    parser = argparse.ArgumentParser(
        description="Download hotspot data from SpinDistill API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download with all defaults (locationd, NapiliD_Seed_3_(23S5611c), N210)
  python dev/download_hotspot.py

  # Download hotspots for a different daemon
  python dev/download_hotspot.py --daemon mediaserverd

  # Download hotspots for mediaserverd with different dataset
  python dev/download_hotspot.py -d mediaserverd -s "Luck_Seed_5_(23A5308g)" -v D83

  # Download with custom output directory
  python dev/download_hotspot.py -d locationd -s "MyBuild_(1.0)" -v N210 --output-dir ./hotspots

  # Override context filter (default is "Unplugged")
  python dev/download_hotspot.py -d locationd -s "MyBuild" -v N210 --context-filter "Overall"
        """
    )

    # All arguments are optional with defaults
    parser.add_argument(
        "-d", "--daemon",
        default=DEFAULT_PROCESS,
        help=f"Daemon/process name (default: {DEFAULT_PROCESS})"
    )
    parser.add_argument(
        "-s", "--dataset",
        default=DEFAULT_DATASET,
        help=f'Dataset identifier (default: "{DEFAULT_DATASET}")'
    )
    parser.add_argument(
        "-v", "--device",
        default=DEFAULT_DEVICE,
        help=f"Device identifier (default: {DEFAULT_DEVICE})"
    )
    parser.add_argument(
        "--context-filter",
        default=DEFAULT_CONTEXT_FILTER,
        help=f"Context filter (default: {DEFAULT_CONTEXT_FILTER})"
    )
    parser.add_argument(
        "--country-code",
        default=DEFAULT_COUNTRY_CODE,
        help=f"Country code (default: {DEFAULT_COUNTRY_CODE})"
    )
    parser.add_argument(
        "--slice-type",
        default=DEFAULT_SLICE_TYPE,
        help=f"Slice type (default: {DEFAULT_SLICE_TYPE})"
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Hotspot Data Downloader")
    print("=" * 60)

    try:
        # Create downloader with configuration from command line arguments
        downloader = HotspotDownloader(
            daemon=args.daemon,
            dataset=args.dataset,
            device=args.device,
            context_filter=args.context_filter,
            country_code=args.country_code,
            slice_type=args.slice_type,
            output_dir=args.output_dir
        )

        # Download hotspot data
        filepath = downloader.download()

        print("=" * 60)
        return 0

    except RuntimeError as e:
        print(f"\n❌ Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n\nDownload cancelled by user.")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())