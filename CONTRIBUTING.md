# Contributing

Use Python 3.10–3.12. Install `requirements-dev.txt`, then run:

```sh
python -m ruff check .
python -m pytest -q
python scripts/privacy_check.py
```

Tests disable desktop notifications and live market scanning, use isolated temporary data, and mock external message delivery. Do not test by sending messages to someone else's account.

Include the concrete behavior changed and its validation in pull requests. Trading rules need deterministic tests for warm-up, rolling windows, reset and cooldown. Include no credentials, market-data exports or personal runtime files. Contributions are licensed under MIT; retain all third-party notices.
