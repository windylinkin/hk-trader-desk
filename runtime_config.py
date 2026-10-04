"""Portable runtime paths. No user-specific path belongs in source control."""

import os
from pathlib import Path

from platformdirs import user_data_path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
DATA_DIR = Path(os.environ.get("HK_DESK_DATA_DIR") or user_data_path("HKTraderDesk", appauthor=False))
FUTU_HOST = os.environ.get("FUTU_HOST", "127.0.0.1")
FUTU_PORT = int(os.environ.get("FUTU_PORT", "11111"))
WEB_PORT = int(os.environ.get("HK_DESK_PORT", "8080"))
DESKTOP_ENABLED = os.environ.get("HK_DESK_DESKTOP", "1") != "0"
DISABLE_MONITOR = os.environ.get("HK_DESK_MONITOR", "1") == "0"
if not 1 <= FUTU_PORT <= 65535 or not 1 <= WEB_PORT <= 65535:
    raise ValueError("Port must be between 1 and 65535")


def prepare_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        DATA_DIR.chmod(0o700)
    return DATA_DIR
