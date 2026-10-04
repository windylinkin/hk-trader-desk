# Third-party notices

This project's original source is MIT licensed. Third-party components retain their own licenses.

- TradingView Lightweight Charts 4.2.3 is vendored under Apache-2.0. Its LICENSE and NOTICE are preserved under `web/vendor`. See https://github.com/tradingview/lightweight-charts and https://www.tradingview.com/ for attribution.
- Futu OpenAPI Python SDK is installed as a dependency. Futu OpenD is proprietary, supplied separately by Futu, and is not redistributed here. Users must comply with their market-data entitlements and applicable service terms.
- FastAPI, Pydantic, HTTPX, platformdirs, python-dotenv and Pillow use their respective upstream licenses. Uvicorn, pandas and keyring likewise retain their upstream licenses.
- pystray is used only on Windows and remains under its upstream LGPL license. It is installed as a dependency rather than bundled into this source distribution.

Review the installed packages' license metadata before distributing a bundled executable. This repository provides source and dependency declarations rather than an all-in-one executable containing third-party runtimes.
