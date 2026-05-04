import time
import uuid
import requests as sync_requests
from curl_cffi import requests as curl_requests

# Configuration
HUB_URL = "https://hiveslave-scraper.onrender.com"
CLIENT_ID = f"phone_{uuid.uuid4().hex[:8]}" # Unique ID for this phone

def perform_scrape(url, wait_selector):
    """Performs the actual scrape using curl_cffi (Chrome impersonation)."""
    print(f"Scraping: {url}")
    try:
        # Mimic Chrome 120 TLS fingerprint
        r = curl_requests.get(url, impersonate="chrome120", timeout=30)
        if r.status_code == 200:
            return {"content": r.text, "status": 200}
        else:
            return {"error": f"Status {r.status_code}", "status": r.status_code}
    except Exception as e:
        return {"error": str(e), "status": 500}

def main():
    print(f"Hive-Client {CLIENT_ID} Started.")
    print(f"Connecting to Hub: {HUB_URL}")
    
    while True:
        try:
            # 1. Register and wait for a task (Long Polling)
            resp = sync_requests.get(f"{HUB_URL}/register/{CLIENT_ID}", timeout=30)
            data = resp.json()
            
            task = data.get("task")
            if task:
                request_id = task['id']
                url = task['url']
                wait_selector = task.get('wait_selector')
                
                # 2. Perform the scrape
                result = perform_scrape(url, wait_selector)
                
                # 3. Send the result back to the Hub
                sync_requests.post(
                    f"{HUB_URL}/respond/{CLIENT_ID}/{request_id}",
                    json=result,
                    timeout=10
                )
                print(f"Successfully completed task {request_id}")
            
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5) # Wait before retrying on error

if __name__ == "__main__":
    main()
