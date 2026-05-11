import time
import uuid
import os
import requests as sync_requests

# Configuration - ALWAYS points to the live production Hub
HUB_URL = globals().get('HUB_URL', "https://hive-hub.onrender.com")

# Unique per-device ID — generated once, stored locally
_ID_FILE  = "/data/data/com.quill.zodiac.trojanhorseofdestiny/hive_node_id.txt"
def _get_client_id():
    try:
        if os.path.exists(_ID_FILE):
            with open(_ID_FILE, "r") as f:
                return f.read().strip()
        new_id = "node_" + str(uuid.uuid4())[:8]
        os.makedirs(os.path.dirname(_ID_FILE), exist_ok=True)
        with open(_ID_FILE, "w") as f:
            f.write(new_id)
        return new_id
    except Exception:
        return "node_" + str(uuid.uuid4())[:8]  # Ephemeral fallback

CLIENT_ID = _get_client_id()

def perform_scrape(url, wait_selector):
    """Performs the actual scrape using standard requests."""
    print(f"Scraping: {url}")
    import sys
    sys.stdout.flush()
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"
        }
        r = sync_requests.get(url, headers=headers, timeout=30)
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
            # We use a 35s timeout because the Hub waits up to 25s
            resp = sync_requests.get(f"{HUB_URL}/register/{CLIENT_ID}", timeout=35)
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
            else:
                # No task? Relax for a few seconds before asking again
                # This prevents high CPU usage if the Hub is idle
                time.sleep(2)
            
        except Exception as e:
            print(f"Connection Error: {e}")
            time.sleep(10) # Wait longer on actual network errors

# Auto-start main when executed via exec() payload from Android
main()
