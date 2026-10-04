"""Telegram / Bark push. Secrets use Windows DPAPI or the OS keychain."""

import datetime as dt
import queue
import re
import threading
import httpx
from pydantic import BaseModel, Field, ConfigDict, StrictBool
from secret_store import crypt, forget


class MobileSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = False
    telegram_enabled: StrictBool = False
    bark_enabled: StrictBool = False
    telegram_token: str = Field(default="", max_length=200)
    telegram_chat_id: str = Field(default="", max_length=100)
    bark_key: str = Field(default="", max_length=200)
    clear_secrets: StrictBool = False


class MobilePush:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.queue = queue.Queue(500)
        self.stop_event = threading.Event()
        self.generation = 0

    def settings(self, private=False):
        saved = self.store.get("mobile", {})
        public = {
            k: saved.get(k, False if k.endswith("enabled") else "")
            for k in ["enabled", "telegram_enabled", "bark_enabled"]
        }
        public["telegram_chat_id_configured"] = bool(saved.get("telegram_chat_id"))
        if private:
            public["telegram_chat_id"] = crypt(saved["telegram_chat_id"], True) if saved.get("telegram_chat_id") else ""
        for key in ["telegram_token", "bark_key"]:
            public[key + "_configured"] = bool(saved.get(key))
            if private:
                public[key] = crypt(saved[key], True) if saved.get(key) else ""
        return public

    def save(self, settings):
        with self.lock:
            previous = self.store.get("mobile", {})
            saved = {} if settings.clear_secrets else dict(previous)
            for key in ["enabled", "telegram_enabled", "bark_enabled"]:
                saved[key] = getattr(settings, key)
            replacements = {}
            for key in ["telegram_token", "bark_key", "telegram_chat_id"]:
                value = getattr(settings, key).strip()
                if value:
                    pattern = (
                        r"[0-9]+:[A-Za-z0-9_-]+"
                        if key == "telegram_token"
                        else r"-?[0-9]+|@[A-Za-z0-9_]{5,}"
                        if key == "telegram_chat_id"
                        else r"[A-Za-z0-9_-]+"
                    )
                    if not re.fullmatch(pattern, value):
                        raise ValueError("密钥格式不正确")
                    replacements[key] = value
            if saved.get("enabled"):
                if not saved.get("telegram_enabled") and not saved.get("bark_enabled"):
                    raise ValueError("请至少选择一个渠道")
                if saved.get("telegram_enabled") and any(
                    not (saved.get(k) or replacements.get(k)) for k in ["telegram_token", "telegram_chat_id"]
                ):
                    raise ValueError("Telegram 需要 Bot Token 和 Chat ID")
                if saved.get("bark_enabled") and not (saved.get("bark_key") or replacements.get("bark_key")):
                    raise ValueError("Bark 需要设备 Key")
            created = []
            try:
                for key, value in replacements.items():
                    saved[key] = crypt(value)
                    created.append(saved[key])
                self.store.put("mobile", saved)
            except Exception:
                for reference in created:
                    forget(reference)
                raise
            self.generation += 1
            for key in ["telegram_token", "bark_key", "telegram_chat_id"]:
                if previous.get(key) and previous.get(key) != saved.get(key):
                    forget(previous[key])
        return self.settings()

    def start(self):
        self.stop_event.clear()
        threading.Thread(target=self.worker, daemon=True, name="mobile-push").start()

    def enqueue(self, title, content):
        with self.lock:
            if not self.settings().get("enabled"):
                return
            try:
                self.queue.put_nowait((self.generation, title, content))
            except queue.Full:
                self.record("all", False, "推送队列已满，详情仍保存在告警历史")

    def record(self, channel, success, detail):
        self.store.delivery(dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), channel, success, detail)

    def send(self, title, content):
        if not self.settings()["enabled"]:
            return [{"channel": "all", "success": False, "detail": "手机推送已关闭"}]
        settings = self.settings(True)
        results = []
        for channel in ["telegram", "bark"]:
            if not settings[channel + "_enabled"]:
                continue
            try:
                if channel == "telegram":
                    url = "https://api.telegram.org/bot" + settings["telegram_token"] + "/sendMessage"
                    payload = {"chat_id": settings["telegram_chat_id"], "text": (title + "\n" + content)[:4000]}
                else:
                    url = "https://api.day.app/push"
                    payload = {
                        "device_key": settings["bark_key"],
                        "title": title[:100],
                        "body": content[:3000],
                        "group": "港股交易工作台",
                    }
                response = httpx.post(url, json=payload, timeout=12, follow_redirects=False)
                data = response.json()
                success = response.status_code == 200 and (
                    data.get("ok") is True if channel == "telegram" else data.get("code") == 200
                )
                detail = (
                    "服务端已接收，手机送达以设备通知为准"
                    if success
                    else "服务端拒绝：请检查密钥、接收目标、机器人权限和网络"
                )
            except Exception:
                success = False
                detail = "请求失败：请检查网络、代理及推送凭据"
            self.record(channel, success, detail)
            results.append(dict(channel=channel, success=success, detail=detail))
        return results

    def worker(self):
        while not self.stop_event.is_set():
            try:
                generation, title, content = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    if generation == self.generation:
                        self.send(title, content)
            except Exception:
                self.record("all", False, "配置读取失败，请重新保存推送凭据")
            finally:
                self.queue.task_done()
            self.stop_event.wait(1.1)
