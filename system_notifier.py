"""Optional local desktop notifications for Windows, macOS, and Linux."""

import json
import logging
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from runtime_config import DATA_DIR, DESKTOP_ENABLED, WEB_PORT, prepare_data_dir

MAC_SCRIPT = """on run argv
    display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"
end run"""


class SystemNotifier:
    def __init__(self, settings_path=None):
        self.settings_path = Path(settings_path) if settings_path else DATA_DIR / "notification_settings.json"
        self.lock = threading.RLock()
        self.enabled = True
        self.generation = 0
        try:
            if self.settings_path.exists():
                self.enabled = bool(json.loads(self.settings_path.read_text(encoding="utf-8")).get("enabled", True))
        except (OSError, ValueError):
            logging.warning("Notification settings unavailable; using defaults")
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.messages = queue.Queue(maxsize=500)
        self.icon = None
        self.last_error = None
        self.backend = "unavailable"

    def start(self):
        self.stopping.clear()
        if not DESKTOP_ENABLED:
            self.backend = "disabled"
            return
        try:
            if sys.platform == "win32":
                # Never import a graphical Windows backend on macOS/headless CI.
                import pystray
                from PIL import Image, ImageDraw

                picture = Image.new("RGB", (64, 64), "#0f172a")
                ImageDraw.Draw(picture).line([(9, 43), (23, 27), (35, 36), (54, 15)], fill="#38bdf8", width=6)
                self.icon = pystray.Icon(
                    "HKTraderDesk",
                    picture,
                    "HK Trader Desk",
                    menu=pystray.Menu(
                        pystray.MenuItem("Open dashboard", self.open_panel, default=True),
                        pystray.MenuItem("Test notification", self.test_notification),
                    ),
                )
                self.backend = "windows-tray"
                threading.Thread(target=self._run_tray, daemon=True, name="desktop-tray").start()
            elif sys.platform == "darwin" and shutil.which("osascript"):
                self.backend = "macos-notification-center"
                self.ready.set()
            elif sys.platform.startswith("linux") and shutil.which("notify-send"):
                self.backend = "linux-notify-send"
                self.ready.set()
            else:
                self.last_error = "Desktop notifications are unavailable; web and mobile alerts still work"
                return
            threading.Thread(target=self._deliver, daemon=True, name="desktop-notifications").start()
        except Exception:
            self.last_error = "Desktop notifications could not start"
            logging.warning(self.last_error)

    def _run_tray(self):
        try:
            self.icon.run(setup=self._setup)
        except Exception:
            self.last_error = "Desktop tray unavailable"
        finally:
            self.ready.clear()

    def _setup(self, icon):
        icon.visible = True
        self.ready.set()

    def open_panel(self, icon=None, item=None):
        webbrowser.open(f"http://localhost:{WEB_PORT}/")

    def test_notification(self, icon=None, item=None):
        if not self.enabled:
            return False
        return self.notify("HK Trader Desk · Test", "Desktop notifications are enabled.")

    def set_enabled(self, enabled):
        with self.lock:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.settings_path.with_suffix(".tmp")
            temp.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
            temp.replace(self.settings_path)
            if sys.platform != "win32":
                self.settings_path.chmod(0o600)
            self.enabled = enabled
            self.generation += 1
            while True:
                try:
                    self.messages.get_nowait()
                    self.messages.task_done()
                except queue.Empty:
                    break
            if not enabled and self.icon and self.ready.is_set():
                try:
                    import winsound

                    self.icon.remove_notification()
                    winsound.PlaySound(None, 0)
                except Exception:
                    pass
        return self.status()

    def notify(self, title, content):
        with self.lock:
            if not self.enabled:
                return True
            if not self.ready.is_set():
                return False
            try:
                self.messages.put_nowait((self.generation, str(title)[:120], str(content)[:1000]))
                return True
            except queue.Full:
                self.last_error = "Notification queue is full; use alert history"
                return False

    def _send(self, title, content):
        if self.backend == "windows-tray":
            import winsound

            self.icon.notify(content[:255], title[:63])
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        elif self.backend == "macos-notification-center":
            # Alert text is passed as argv, never interpolated into AppleScript.
            subprocess.run(["osascript", "-e", MAC_SCRIPT, title, content], check=True, capture_output=True, timeout=10)
        elif self.backend == "linux-notify-send":
            subprocess.run(["notify-send", "--", title, content], check=True, capture_output=True, timeout=10)

    def _deliver(self):
        while not self.stopping.is_set():
            try:
                generation, title, content = self.messages.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    if not self.enabled or generation != self.generation:
                        continue
                    self._send(title, content)
                    self.last_error = None
            except Exception:
                self.last_error = "System notification failed; check OS notification permissions"
            finally:
                self.messages.task_done()
            self.stopping.wait(5)

    def status(self):
        return dict(
            enabled=self.enabled,
            ready=self.ready.is_set(),
            backend=self.backend,
            pending=self.messages.qsize(),
            error=self.last_error,
        )

    def stop(self):
        self.stopping.set()
        self.ready.clear()
        if self.icon:
            self.icon.stop()


notifier = SystemNotifier()
