import asyncio
import csv
import datetime as dt
import io
import json
import math
import socket
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, model_validator
from futu import OpenQuoteContext, Market, SecurityType, RET_OK
from workstation_store import store, ROOT
from pattern_engine import Pattern, PatternEngine, DEFAULT_PATTERNS
from mobile_push import MobilePush, MobileSettings
from market_analysis import history
from system_notifier import notifier
from runtime_config import FUTU_HOST, FUTU_PORT, DISABLE_MONITOR

HK = dt.timezone(dt.timedelta(hours=8))
mobile = MobilePush(store)
stop = threading.Event()
state_lock = threading.RLock()
web_loop = None
clients = set()
status = {
    "status": "initializing",
    "status_message": "正在连接富途 OpenD",
    "total_stocks": 0,
    "scan_count": 0,
    "last_scan_time": None,
    "start_time": dt.datetime.now(HK).strftime("%Y-%m-%d %H:%M:%S"),
    "scan_seconds": 0,
    "coverage": 0,
    "last_error": None,
}
universe = []
live_rows = []
patterns = [Pattern(**p) for p in store.get("patterns", DEFAULT_PATTERNS)]
settings = store.get("scanner", {"scan_interval": 5, "watchlist": []})


def get_status():
    with state_lock:
        result = dict(status)
    result.update(
        app_id="hk-trader-desk",
        alert_count=store.count(),
        uptime=str(
            dt.datetime.now(HK) - dt.datetime.strptime(result["start_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=HK)
        ).split(".")[0],
        system_notifications=notifier.status(),
        mobile_enabled=mobile.settings()["enabled"],
    )
    return result


def set_status(value, message):
    with state_lock:
        status.update(status=value, status_message=message)


async def broadcast(message):
    for ws in list(clients):
        try:
            await asyncio.wait_for(ws.send_json(message), timeout=2)
        except Exception:
            clients.discard(ws)


def publish(alert):
    store.add_alert(alert)
    notifier.notify(alert["title"], alert["detail"])
    if alert.get("mobile"):
        mobile.enqueue(alert["title"], alert["detail"] + "\n香港时间 " + alert["timestamp"])
    if web_loop and not web_loop.is_closed():
        asyncio.run_coroutine_threadsafe(broadcast({"type": "alert", "data": alert}), web_loop)


def session(now):
    if now.weekday() >= 5:
        return None
    minute = now.hour * 60 + now.minute
    if 570 <= minute < 720:
        return "morning"
    if 780 <= minute < 960:
        return "afternoon"
    return None


def update_outcomes(pending, rows, now, current_session):
    prices = {r["code"]: r["last_price"] for r in rows}
    retained = []
    for alert, done, origin_session in pending:
        if (
            origin_session != current_session
            or dt.datetime.fromtimestamp(alert["epoch"], HK).date() != dt.datetime.fromtimestamp(now, HK).date()
        ):
            continue
        elapsed = now - alert["epoch"]
        price = prices.get(alert["code"])
        for horizon in (300, 900, 1800):
            if horizon in done:
                continue
            if elapsed > horizon + 60:
                done.add(horizon)
                continue
            if elapsed >= horizon and price and math.isfinite(price) and price > 0:
                store.outcome(alert["id"], horizon, price, elapsed, (price / alert["price"] - 1) * 100)
                done.add(horizon)
        if len(done) < 3:
            retained.append((alert, done, origin_session))
    return retained


def monitor_worker():
    global universe, live_rows
    request_times = []
    while not stop.is_set():
        ctx = None
        try:
            set_status("connecting", "正在连接富途 OpenD")
            with socket.create_connection((FUTU_HOST, FUTU_PORT), timeout=3):
                pass
            ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
            ret, frame = ctx.get_stock_basicinfo(Market.HK, SecurityType.STOCK)
            if ret != RET_OK:
                raise RuntimeError("股票列表获取失败，请检查 OpenD 权限")
            frame = frame[(frame.exchange_type == "HK_MAINBOARD") & (frame.delisting == False)]
            codes = frame.code.tolist()
            names = dict(zip(frame.code, frame.name))
            with state_lock:
                universe = [dict(code=c, name=names[c]) for c in codes]
                status["total_stocks"] = len(codes)
            engine = PatternEngine()
            previous = None
            trading = []
            calendar_day = None
            pending = []
            while not stop.is_set():
                now = dt.datetime.now(HK)
                current = session(now)
                session_key = (str(now.date()), current)
                if session_key != previous:
                    engine.reset()
                    pending = []
                    previous = session_key
                if not current:
                    set_status("sleeping", "非交易时段，等待开盘")
                    stop.wait(10)
                    continue
                today = str(now.date())
                if calendar_day != today:
                    ret, trading = ctx.request_trading_days(Market.HK, start=today, end=today)
                    if ret != RET_OK:
                        raise RuntimeError("交易日历查询失败")
                    calendar_day = today
                if not trading or (current == "afternoon" and trading[0]["trade_date_type"] != "WHOLE"):
                    set_status("sleeping", "港股休市，等待下一交易时段")
                    stop.wait(30)
                    continue
                with state_lock:
                    rules = list(patterns)
                    config = dict(settings)
                begin = time.monotonic()
                rows = []
                for offset in range(0, len(codes), 400):
                    request_times = [t for t in request_times if time.monotonic() - t < 30.2]
                    if len(request_times) >= 58:
                        stop.wait(max(0, 30.2 - (time.monotonic() - request_times[0])))
                    if stop.is_set():
                        return
                    request_times.append(time.monotonic())
                    ret, snap = ctx.get_market_snapshot(codes[offset : offset + 400])
                    if ret != RET_OK:
                        raise RuntimeError("快照失败，请检查行情权限、配额或网关连接")
                    sampled = time.time()
                    for r in snap.to_dict("records"):
                        r["name"] = names.get(r["code"], r["code"])
                        r["_sample_time"] = sampled
                        rows.append(r)
                # Do not issue alerts from a scan that crossed the market break.
                if session(dt.datetime.now(HK)) != current:
                    continue
                for r in rows:
                    ts = r["_sample_time"]
                    stamp = dt.datetime.fromtimestamp(ts, HK).strftime("%Y-%m-%d %H:%M:%S")
                    for alert in engine.evaluate(r, ts, stamp, rules, set(config["watchlist"])):
                        publish(alert)
                        pending.append((alert, set(), session_key))
                pending = update_outcomes(pending, rows, time.time(), session_key)
                summary = []
                for r in rows:
                    price = float(r["last_price"])
                    prev = float(r.get("prev_close_price", 0))
                    turnover = float(r.get("turnover", 0))
                    if price > 0 and all(map(math.isfinite, [price, prev, turnover])):
                        summary.append(
                            dict(
                                code=r["code"],
                                name=r["name"],
                                price=price,
                                change_pct=(price / prev - 1) * 100 if prev else 0,
                                turnover=turnover,
                            )
                        )
                with state_lock:
                    live_rows = sorted(summary, key=lambda r: r["turnover"], reverse=True)
                    status.update(
                        scan_count=status["scan_count"] + 1,
                        last_scan_time=dt.datetime.now(HK).strftime("%Y-%m-%d %H:%M:%S"),
                        scan_seconds=round(time.monotonic() - begin, 2),
                        coverage=len(rows),
                        last_error=None,
                    )
                set_status("running", "监控运行中 · 规则基于快照采样")
                stop.wait(max(0, config["scan_interval"] - (time.monotonic() - begin)))
        except Exception as exc:
            message = (
                "等待 OpenD 启动并登录，将自动重连"
                if isinstance(exc, OSError)
                else "监控读取失败，请检查网关连接和行情权限"
            )
            set_status("disconnected", message)
            with state_lock:
                status["last_error"] = message
            print("[monitor]", message, flush=True)
            stop.wait(10)
        finally:
            if ctx:
                ctx.close()


@asynccontextmanager
async def lifespan(app):
    global web_loop
    web_loop = asyncio.get_running_loop()
    stop.clear()
    notifier.start()
    mobile.start()
    worker = threading.Thread(target=monitor_worker, daemon=True)
    if not DISABLE_MONITOR:
        worker.start()
    else:
        set_status("sleeping", "监控已通过环境设置暂停")
    yield
    stop.set()
    mobile.stop_event.set()
    notifier.stop()
    if worker.is_alive():
        await web_loop.run_in_executor(None, worker.join, 5)


app = FastAPI(title="港股交易工作台", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def private_validation_error(request, exc):
    return JSONResponse(
        {"detail": [{"loc": e["loc"], "type": e["type"], "msg": e["msg"]} for e in exc.errors()]}, status_code=422
    )


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.middleware("http")
async def local_requests(request: Request, call_next):
    # Credentials and configuration are local-only; push reaches phones via providers.
    host = urlparse("http://" + request.headers.get("host", "")).hostname
    if host not in ("localhost", "127.0.0.1", "::1", "testserver"):
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "web" / "workstation.html").read_text(encoding="utf-8")


@app.get("/api/status")
def api_status():
    return get_status()


@app.get("/api/alerts")
def alerts(limit: int = Query(200, ge=1, le=2000), code: str = "", date: str = "", pattern: str = ""):
    return {"alerts": store.alerts(limit, code, date, pattern)}


@app.get("/api/patterns")
def get_patterns():
    with state_lock:
        return {"patterns": [p.model_dump() for p in patterns], "scanner": dict(settings)}


class PatternConfig(BaseModel):
    patterns: List[Pattern] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique(self):
        if len(set(p.id for p in self.patterns)) != len(self.patterns):
            raise ValueError("模式ID不能重复")
        return self


@app.post("/api/patterns")
def save_patterns(body: PatternConfig):
    global patterns
    with state_lock:
        store.put("patterns", [p.model_dump() for p in body.patterns])
        patterns = body.patterns
    return {"success": True, "message": "已保存，下一轮生效；修改规则会重新预热采样窗口"}


class ScannerConfig(BaseModel):
    scan_interval: int = Field(default=5, ge=5, le=300)
    watchlist: List[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid(self):
        import re

        if any(not re.fullmatch(r"HK\.[0-9]{5}", c) for c in self.watchlist):
            raise ValueError("自选格式应为 HK.00700")
        self.watchlist = list(dict.fromkeys(self.watchlist))
        return self


@app.post("/api/scanner")
def save_scanner(body: ScannerConfig):
    global settings
    with state_lock:
        settings = body.model_dump()
        store.put("scanner", settings)
    return settings


@app.get("/api/stocks")
def stocks(q: str = ""):
    with state_lock:
        return {"stocks": [r for r in universe if q.lower() in (r["code"] + r["name"]).lower()][:60]}


@app.get("/api/market")
def market():
    with state_lock:
        return {
            "rows": live_rows[:80],
            "watchlist": [r for r in live_rows if r["code"] in settings["watchlist"]],
            "asof": status["last_scan_time"],
        }


@app.get("/api/chart")
def chart(
    code: str = Query(pattern=r"^HK\.[0-9]{5}$"),
    period: Literal["K_1M", "K_5M", "K_15M", "K_60M", "K_DAY"] = "K_DAY",
    end: str = "",
):
    try:
        end = end or str(dt.datetime.now(HK).date())
        dt.date.fromisoformat(end)
        result = dict(history.get(code, period, end))
        result["alerts"] = store.alerts(500, code=code)
        return result
    except ValueError as exc:
        raise HTTPException(503, detail=str(exc)[:250])
    except Exception:
        raise HTTPException(503, detail="行情不可用，请检查 OpenD 连接和权限")


class ReviewNote(BaseModel):
    alert_id: str = Field(max_length=100)
    tag: Literal["待复盘", "有效信号", "噪声", "已跟踪", "已忽略"] = "待复盘"
    note: str = Field(default="", max_length=5000)


@app.post("/api/review/note")
def save_note(body: ReviewNote):
    try:
        store.note(body.alert_id, body.tag, body.note, dt.datetime.now(HK).isoformat())
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"success": True}


@app.get("/api/review")
def review(date: str = "", pattern: str = ""):
    alerts = store.alerts(2000, date=date, pattern=pattern)
    by_pattern = {}
    for a in alerts:
        key = a.get("type", "未知")
        group = by_pattern.setdefault(
            key,
            {
                "name": key,
                "signals": 0,
                "reviewed": 0,
                "samples": 0,
                "mean_15m": None,
                "positive_rate": None,
                "_returns": [],
            },
        )
        group["signals"] += 1
        group["reviewed"] += a["review"]["tag"] != "待复盘"
        if "900" in a["outcomes"]:
            group["_returns"].append(a["outcomes"]["900"]["return_pct"])
    for g in by_pattern.values():
        values = g.pop("_returns")
        g["samples"] = len(values)
        if values:
            g["mean_15m"] = sum(values) / len(values)
            g["positive_rate"] = sum(v > 0 for v in values) / len(values) * 100
    return {
        "alerts": alerts,
        "groups": list(by_pattern.values()),
        "total": len(alerts),
        "limit": 2000,
        "method": "收益为告警价到后续快照价的原始价格变化，不代表成交收益；仅同一交易时段且目标时点后60秒内有样本时记录。无样本显示空值。",
    }


@app.get("/api/review/export")
def export(date: str = ""):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["时间", "代码", "名称", "模式", "告警价", "5分钟变化%", "15分钟变化%", "30分钟变化%", "标签", "笔记"]
    )

    def safe(v):
        s = str(v)
        return "'" + s if s.startswith(("=", "+", "-", "@")) else s

    for a in store.alerts(10000, date=date):
        writer.writerow(
            [safe(a.get(k, "")) for k in ["timestamp", "code", "name", "type", "price"]]
            + [a["outcomes"].get(str(h), {}).get("return_pct", "") for h in (300, 900, 1800)]
            + [safe(a["review"]["tag"]), safe(a["review"]["note"])]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="review.csv"'},
    )


class Toggle(BaseModel):
    enabled: StrictBool


@app.get("/api/notifications")
def get_notifications():
    return notifier.status()


@app.post("/api/notifications")
def set_notifications(body: Toggle):
    return notifier.set_enabled(body.enabled)


@app.post("/api/notifications/test")
def test_notifications():
    return {"success": notifier.test_notification()}


@app.get("/api/mobile")
def get_mobile():
    return {"settings": mobile.settings(), "deliveries": store.deliveries()}


@app.post("/api/mobile")
def save_mobile(body: MobileSettings):
    try:
        return mobile.save(body)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/mobile/test")
def test_mobile():
    return {"results": mobile.send("港股交易工作台 · 测试提醒", "这是一条由你点击测试按钮发送的手机提醒。")}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    host = urlparse("http://" + ws.headers.get("host", "")).hostname
    if host not in ("localhost", "127.0.0.1", "::1", "testserver"):
        await ws.close(code=1008)
        return
    origin = ws.headers.get("origin")
    if origin and urlparse(origin).netloc != ws.headers.get("host"):
        await ws.close(code=1008)
        return
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_json({"type": "status", "data": get_status()})
        while True:
            await ws.receive_text()
            await ws.send_json({"type": "status", "data": get_status()})
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
