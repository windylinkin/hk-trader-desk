"""Start or reuse the local dashboard. No installers or credentials are launched."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

from runtime_config import ROOT, WEB_PORT, DATA_DIR, prepare_data_dir


def ready():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{WEB_PORT}/api/status", timeout=2) as response:
            payload = json.load(response)
            return payload.get("app_id") == "hk-trader-desk"
    except Exception:
        return False


def main():
    if ready():
        webbrowser.open(f"http://localhost:{WEB_PORT}/")
        return 0
    # An unrelated process must not be mistaken for this dashboard.
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", WEB_PORT)) == 0:
            print("Dashboard port is occupied by another service.", file=sys.stderr)
            return 1
    directory = prepare_data_dir()
    logfile = directory / "startup.log"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    options = {"cwd": str(ROOT), "env": env}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    with logfile.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "web_monitor.py")], stdout=log, stderr=log, **options
        )
    for _ in range(40):
        if ready():
            webbrowser.open(f"http://localhost:{WEB_PORT}/")
            return 0
        if process.poll() is not None:
            break
        time.sleep(0.5)
    print("Could not start the dashboard. Check startup.log in the application-data folder.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
