"""Tests never read an operator's real database or push credentials."""

import os
import tempfile

_test_data = tempfile.TemporaryDirectory(prefix="hk-desk-tests-")
os.environ["HK_DESK_DATA_DIR"] = _test_data.name
os.environ["HK_DESK_DESKTOP"] = "0"
os.environ["HK_DESK_MONITOR"] = "0"


def pytest_sessionfinish(session, exitstatus):
    _test_data.cleanup()
