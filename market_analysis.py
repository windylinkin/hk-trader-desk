import datetime as dt
import socket
import threading
import time
import pandas as pd
from futu import OpenQuoteContext, RET_OK, AuType
from runtime_config import FUTU_HOST, FUTU_PORT


def indicators(bars):
    if not bars:
        return {"series": {}, "summary": {}}
    f = pd.DataFrame(bars)
    c = f.close
    result = {}
    for n in (5, 20, 60):
        result["ma" + str(n)] = c.rolling(n).mean()
    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    result["macd"] = ema12 - ema26
    result["signal"] = result["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    result["histogram"] = result["macd"] - result["signal"]
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    result["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
    result["rsi"] = result["rsi"].mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)
    tr = pd.concat([f.high - f.low, (f.high - c.shift()).abs(), (f.low - c.shift()).abs()], axis=1).max(axis=1)
    result["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    volume_mean = f.volume.shift(1).rolling(20).mean()
    result["relative_volume"] = f.volume / volume_mean.replace(0, float("nan"))
    serial = {
        name: [
            {"time": int(f.time.iloc[i]), "value": float(v)}
            for i, v in enumerate(values)
            if pd.notna(v) and abs(v) != float("inf")
        ]
        for name, values in result.items()
    }
    summary = {k: (v[-1]["value"] if v else None) for k, v in serial.items()}
    summary.update(
        last=float(c.iloc[-1]), bars=len(bars), high20=float(f.high.tail(20).max()), low20=float(f.low.tail(20).min())
    )
    return {"series": serial, "summary": summary}


class HistoryService:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}
        self.last_request = 0

    def get(self, code, period, end_date):
        key = (code, period, end_date)
        with self.lock:
            if key in self.cache and time.monotonic() - self.cache[key][0] < 60:
                return self.cache[key][1]
            if time.monotonic() - self.last_request < 2:
                raise ValueError("历史行情请求较快，请稍后重试")
            self.last_request = time.monotonic()
            with socket.create_connection((FUTU_HOST, FUTU_PORT), timeout=3):
                pass
            ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
            try:
                end = dt.date.fromisoformat(end_date)
                days = 550 if period == "K_DAY" else (25 if period == "K_60M" else 7)
                ret, frame, page = ctx.request_history_kline(
                    code,
                    start=str(end - dt.timedelta(days=days)),
                    end=str(end),
                    ktype=period,
                    autype=AuType.NONE,
                    max_count=1000,
                )
                if ret != RET_OK:
                    raise ValueError("历史行情不可用，请检查 OpenD 登录、行情权限和配额")
                frames = [frame]
                # Fetch bounded pages; never silently present old first-page data as latest.
                for _ in range(5):
                    if page is None:
                        break
                    ret, frame, page = ctx.request_history_kline(
                        code,
                        start=str(end - dt.timedelta(days=days)),
                        end=str(end),
                        ktype=period,
                        autype=AuType.NONE,
                        max_count=1000,
                        page_req_key=page,
                    )
                    if ret != RET_OK:
                        raise ValueError("历史行情分页失败")
                    frames.append(frame)
                if page is not None:
                    raise ValueError("数据量过大，请改用较大K线周期")
                frame = pd.concat(frames).drop_duplicates("time_key").sort_values("time_key").tail(500)
                bars = []
                for row in frame.to_dict("records"):
                    # Shift displayed timestamps into a wall-clock axis labelled Hong Kong time.
                    t = int(
                        dt.datetime.strptime(row["time_key"], "%Y-%m-%d %H:%M:%S")
                        .replace(tzinfo=dt.timezone.utc)
                        .timestamp()
                    )
                    bar = dict(
                        time=t,
                        date=row["time_key"],
                        **{k: float(row[k]) for k in ["open", "high", "low", "close", "volume"]},
                    )
                    if (
                        all(pd.notna(bar[k]) for k in ["open", "high", "low", "close", "volume"])
                        and all(__import__("math").isfinite(bar[k]) for k in ["open", "high", "low", "close", "volume"])
                        and bar["close"] > 0
                    ):
                        bars.append(bar)
                result = dict(
                    code=code,
                    period=period,
                    bars=bars,
                    adjustment="不复权",
                    timezone="香港时间",
                    source="富途历史K线",
                    **indicators(bars),
                )
                self.cache[key] = (time.monotonic(), result)
                if len(self.cache) > 30:
                    self.cache.pop(next(iter(self.cache)))
                return result
            finally:
                ctx.close()


history = HistoryService()
