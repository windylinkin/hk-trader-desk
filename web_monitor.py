# -*- coding: utf-8 -*-
"""Stable launcher; desktop shortcut and Start-Monitor.ps1 continue to use this file."""

from workstation_app import app
from runtime_config import WEB_PORT

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=WEB_PORT, log_level="warning")
