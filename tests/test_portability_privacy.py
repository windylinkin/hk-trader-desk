import importlib
import json
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
import workstation_app
import secret_store
import system_notifier
from mobile_push import MobilePush, MobileSettings
from workstation_store import Store


def test_mac_keychain_reference_is_not_plaintext():
    backend = Mock()
    backend.get_password.return_value = "synthetic-secret"
    with (
        patch.object(secret_store.sys, "platform", "darwin"),
        patch.object(secret_store, "secure_backend", return_value=backend),
    ):
        ciphertext = secret_store.protect("synthetic-secret")
        assert ciphertext.startswith("keyring:")
        assert "synthetic-secret" not in ciphertext
        assert secret_store.unprotect(ciphertext) == "synthetic-secret"
        secret_store.forget(ciphertext)
    backend.set_password.assert_called_once()
    backend.delete_password.assert_called_once()


def test_keychain_failure_never_returns_plaintext():
    with (
        patch.object(secret_store.sys, "platform", "darwin"),
        patch.object(secret_store, "secure_backend", side_effect=RuntimeError("unavailable")),
    ):
        with pytest.raises(OSError, match="not saved"):
            secret_store.protect("synthetic-secret")


def test_macos_notification_arguments_cannot_become_code(tmp_path):
    notifier = system_notifier.SystemNotifier(tmp_path / "notification.json")
    notifier.backend = "macos-notification-center"
    suspicious = '" & do shell script "echo synthetic"'
    with patch.object(system_notifier.subprocess, "run") as runner:
        notifier._send(suspicious, "body")
    argv = runner.call_args.args[0]
    assert argv[:2] == ["osascript", "-e"]
    assert suspicious not in argv[2]
    assert argv[3] == suspicious
    assert "shell" not in runner.call_args.kwargs


def test_optional_desktop_does_not_block_headless_import(tmp_path):
    notifier = system_notifier.SystemNotifier(tmp_path / "notification.json")
    with patch.object(system_notifier, "DESKTOP_ENABLED", False):
        notifier.start()
    assert notifier.status()["backend"] == "disabled"
    assert not notifier.status()["ready"]


def test_muting_clears_queued_notifications(tmp_path):
    notifier = system_notifier.SystemNotifier(tmp_path / "notification.json")
    notifier.ready.set()
    assert notifier.notify("a", "b")
    notifier.set_enabled(False)
    assert notifier.messages.empty()
    assert notifier.notify("a", "b")
    assert not notifier.test_notification()
    assert not system_notifier.SystemNotifier(tmp_path / "notification.json").enabled


def test_secret_validation_error_does_not_echo_inputs():
    client = TestClient(workstation_app.app)
    synthetic_secret = "private-test-content-" * 50
    response = client.post("/api/mobile", json={"telegram_token": synthetic_secret})
    assert response.status_code == 422
    assert synthetic_secret not in response.text
    assert "input" not in response.json()["detail"][0]


def test_remote_origin_and_host_cannot_access_private_settings():
    client = TestClient(workstation_app.app)
    assert client.get("/api/mobile", headers={"Host": "attacker.example"}).status_code == 403
    assert client.post("/api/mobile", json={}, headers={"Origin": "https://attacker.example"}).status_code == 403
    with pytest.raises(Exception):
        with client.websocket_connect("/ws", headers={"Host": "attacker.example"}):
            pass


def test_mobile_redacts_receiving_identity_and_rolls_back_keychain(tmp_path):
    store = Store(tmp_path / "test.db")
    mobile = MobilePush(store)
    with patch("mobile_push.crypt", side_effect=lambda s: "keyring:" + s), patch("mobile_push.forget"):
        mobile.save(MobileSettings(telegram_token="123:synthetic", telegram_chat_id="123"))
    response = mobile.settings()
    assert "telegram_token" not in response
    assert "telegram_chat_id" not in response
    assert response["telegram_chat_id_configured"]
    with (
        patch("mobile_push.crypt", return_value="keyring:temporary"),
        patch.object(store, "put", side_effect=RuntimeError("db unavailable")),
        patch("mobile_push.forget") as cleanup,
    ):
        with pytest.raises(RuntimeError):
            mobile.save(MobileSettings(bark_key="synthetic"))
        cleanup.assert_called_once_with("keyring:temporary")


def test_static_mount_cannot_download_private_runtime_files():
    client = TestClient(workstation_app.app)
    assert client.get("/static/data/workstation.sqlite3").status_code == 404
    assert client.get("/static/.env").status_code == 404
    assert client.get("/static/../secret_store.py").status_code != 200
