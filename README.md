# HK Trader Desk

A local-first Hong Kong stock monitoring and review workspace powered by Futu OpenD.

[中文说明](README.zh-CN.md) · [Privacy](docs/PRIVACY.md) · [macOS setup](docs/MACOS.md) · [Contributing](CONTRIBUTING.md)

## Features

- Configurable drawdown, volume and breakout patterns with warmup, cooldown, liquidity filters and watchlists.
- Persistent alerts, parameter snapshots, review notes, CSV export and observed 5/15/30-minute price changes.
- Historical candles, MA5/20/60, RSI, MACD, ATR, relative volume and candle replay using locally bundled TradingView Lightweight Charts™.
- Optional Windows or macOS desktop notifications, Telegram messages, and Bark notifications for iPhone.
- Risk-budget arithmetic for a manually entered long-trade plan.
- Loopback-only web server; no analytics, automatic uploads or bundled credentials.

## Requirements

Python **3.10–3.12**, installed Futu OpenD, and the required market-data entitlements. Obtain OpenD from the [official Futu documentation](https://openapi.futunn.com/futu-api-doc/en/quick/opend-base.html). This repository does not redistribute OpenD, account credentials or market data.

| Platform | Application | Desktop notifications | Credentials |
|---|---|---|---|
| Windows | Local browser, PowerShell launcher | Optional system tray | Windows DPAPI |
| macOS, Intel / Apple Silicon | Safari or other local browser, `.command` launcher | Notification Center via `osascript` | macOS Keychain |
| Linux | Local browser | Optional `notify-send` | Secret Service, when available |
| iPhone | Bark notifications | Bark app | Device key saved on the monitoring computer |

The repository includes portable implementations and simulated platform tests. Windows has been locally tested. Native macOS notifications, Apple Silicon dependencies and mobile delivery still require verification on the user's devices; configuring macOS CI does not by itself prove a CI run passed.

## Install and run

Clone or download this repository, then open its folder. Install OpenD separately, start it, and log in. Its default quote port is `11111`.

macOS / Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python launch.py
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\Start-Monitor.ps1
```

The dashboard opens at `http://localhost:8080/`. Run `web_monitor.py` directly to keep the server in the foreground; press Ctrl+C to stop it. `launch.py` starts the server in the background and reuses an existing instance. Close that process when you want monitoring to stop.

Copy `.env.example` to `.env` for optional ports, gateway host, application-data folder, or desktop/monitor disabling. Push credentials belong in the dashboard's settings screen, **not** in `.env` or source code.

## Mobile notifications

Telegram requires a bot token, a chat ID, and a started bot conversation. Bark requires its iPhone device key and uses `https://api.day.app/push`. Save the configuration, then click the test button. A provider accepting a message does not prove the phone displayed it. The monitoring computer needs network access to the provider.

Secrets and receiving chat IDs are encrypted or stored in the OS keychain. They are never returned by the configuration API. Empty fields retain the previous value. Saving fails if secure secret storage is unavailable; there is no plaintext fallback.

## Data and analysis

Runtime data lives in the OS application-data folder, outside the repository, unless explicitly overridden. See [privacy and backup instructions](docs/PRIVACY.md).

Signals use observed snapshots, not exchange tick data. Windows warm up after startup, rule changes and session breaks. Candle prices are unadjusted and may contain corporate-action gaps. Forward-price observations are recorded only within the same day/session, when a snapshot arrives within 60 seconds after the target horizon. Missing observations remain missing, including after interruption. The review statistics are signal research, not executed profit, a win rate, or a full trading backtest. This application does not place orders.

## Tests and publish audit

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check .
python scripts/privacy_check.py
```

The privacy audit requires a Git repository with staged/tracked public files. It checks that exact file set and prints finding categories without printing secret values. It supplements manual review; no scanner can prove that arbitrary data is non-sensitive.

CI is configured for Windows, macOS and Ubuntu on Python 3.10 and 3.12. Tests use temporary stores and mocked external push services; they do not require a Futu or Telegram account.

## License and attribution

Project code: [MIT](LICENSE). Bundled Lightweight Charts™ 4.2.3: Apache-2.0, with its [LICENSE](web/vendor/LICENSE-lightweight-charts.txt), [NOTICE](web/vendor/NOTICE-lightweight-charts.txt) and TradingView attribution retained. Other dependencies keep their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
