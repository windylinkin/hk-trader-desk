# macOS and iPhone

Install Python 3.10–3.12 and the official macOS Futu OpenD separately. Open OpenD, sign in locally, and enable the local API on port 11111. This project never installs OpenD or stores its account password.

In Terminal, from this repository:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
chmod +x Start-Monitor.command
./Start-Monitor.command
```

Safari opens the dashboard at http://localhost:8080. If Finder refuses a downloaded command file, run it from Terminal after reviewing it. Approve macOS notifications when prompted; desktop notifications use the system's AppleScript notification service. There is no macOS tray menu. Browser sound may require clicking the dashboard first.

For iPhone, install Bark, enable its notifications, and copy its device key into the dashboard's mobile notification settings. Enable Bark and the mobile notification switch, save, then use the explicit test button. Alternatively configure Telegram with your own bot token and receiving chat ID. Empty credential fields preserve existing values. Clearing credentials removes stored references.

macOS credentials are stored in Keychain; the local database contains references. A locked or unavailable Keychain produces an error rather than falling back to plaintext. Keep the computer, OpenD, and monitor running for continuous alerts. Phone delivery also depends on internet access, service availability, and Focus settings.

The server remains local to the computer. An iPhone browser cannot access this loopback dashboard. This release targets macOS desktop use and iPhone push notifications. macOS-specific behavior is covered by mocked tests and a CI job, but still requires validation on actual Mac hardware.
