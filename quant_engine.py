"""Long-only, next-bar paper execution shared by HK equities and BTC spot."""

import math
import threading
import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator, ConfigDict
from market_analysis import history


class QuantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["hk", "btc"] = "btc"
    symbol: str = Field(default="BTC/USDT", max_length=30)
    exchange: Literal["okx", "binance", "kraken"] = "okx"
    period: Literal["K_1M", "K_5M", "K_15M", "K_60M", "K_DAY"] = "K_60M"
    end: str = Field(default="", max_length=10)
    fast: int = Field(default=10, ge=2, le=100)
    slow: int = Field(default=30, ge=3, le=200)
    capital: float = Field(default=100000, gt=0, le=1e10)
    allocation: float = Field(default=50, gt=0, le=100)
    fee_bps: float = Field(default=10, ge=0, le=500)
    slippage_bps: float = Field(default=5, ge=0, le=500)
    lot_size: int = Field(default=100, ge=1, le=100000)

    @model_validator(mode="after")
    def validate_parameters(self):
        import re

        if self.fast >= self.slow:
            raise ValueError("快线必须小于慢线")
        if self.market == "hk" and not re.fullmatch(r"HK\.[0-9]{5}", self.symbol):
            raise ValueError("港股代码格式为 HK.00700")
        if self.market == "btc" and self.symbol not in ("BTC/USDT", "BTC/USD"):
            raise ValueError("本版支持 BTC/USDT 与 BTC/USD 现货")
        if not all(math.isfinite(v) for v in (self.capital, self.allocation, self.fee_bps, self.slippage_bps)):
            raise ValueError("参数必须是有限数值")
        return self


_cache = {}
_lock = threading.Lock()


def crypto_bars(body):
    """Public CCXT OHLCV only; no keys or exchange order endpoints."""
    import ccxt
    import datetime as dt

    timeframe = {"K_1M": "1m", "K_5M": "5m", "K_15M": "15m", "K_60M": "1h", "K_DAY": "1d"}[body.period]
    interval = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}[timeframe]
    cutoff = int(time.time())
    if body.end:
        cutoff = min(
            cutoff,
            int(
                dt.datetime.combine(
                    dt.date.fromisoformat(body.end) + dt.timedelta(days=1), dt.time(), dt.timezone.utc
                ).timestamp()
            ),
        )
    key = (body.exchange, body.symbol, timeframe, body.end)
    with _lock:
        if key in _cache and time.monotonic() - _cache[key][0] < 30:
            return _cache[key][1]
        exchange = getattr(ccxt, body.exchange)({"enableRateLimit": True, "timeout": 12000, "requests_trust_env": True})
        since = (cutoff - 500 * interval) * 1000
        records = []
        for _ in range(6):
            page = exchange.fetch_ohlcv(body.symbol, timeframe, since=since, limit=100)
            if not page:
                break
            records.extend(page)
            next_since = page[-1][0] + interval * 1000
            if next_since <= since or next_since >= cutoff * 1000:
                break
            since = next_since
        bars = {}
        for row in records:
            ts, o, h, l, c, v = row[:6]
            if ts / 1000 + interval > cutoff:
                continue  # never use the current unclosed candle
            if not all(math.isfinite(float(x)) for x in row[:6]) or min(o, h, l, c) <= 0 or v < 0:
                continue
            if h < max(o, c, l) or l > min(o, c, h):
                continue
            bars[int(ts / 1000)] = dict(time=int(ts / 1000), open=o, high=h, low=l, close=c, volume=v)
        result = [bars[k] for k in sorted(bars)][-500:]
        if not result:
            raise ValueError("没有可用的已收盘比特币行情")
        if len(_cache) >= 32:
            _cache.clear()
        _cache[key] = (time.monotonic(), result)
        return result


def get_bars(body):
    import datetime as dt

    if body.market == "btc":
        return crypto_bars(body)
    end = body.end or dt.date.today().isoformat()
    bars = history.get(body.symbol, body.period, end)["bars"]
    duration = {"K_1M": 60, "K_5M": 300, "K_15M": 900, "K_60M": 3600, "K_DAY": 57600}[body.period]
    # Futu chart timestamps use a Hong Kong wall-clock axis, not UTC instants.
    wall_now = time.time() + 8 * 3600
    return [bar for bar in bars if bar["time"] + duration <= wall_now]


def simulate(bars, body):
    if len(bars) < body.slow + 2:
        raise ValueError("历史样本不足，请减小慢线或换周期")
    cash, quantity = body.capital, 0.0
    fee, slip = body.fee_bps / 10000, body.slippage_bps / 10000
    pending = None
    trades, curve, closes = [], [], []
    previous, peak, drawdown = None, body.capital, 0.0
    entry_cost, realized = 0.0, []
    for bar in bars:
        if not all(math.isfinite(float(bar[k])) and bar[k] > 0 for k in ("open", "close")):
            raise ValueError("行情包含无效价格")
        if pending == "buy" and quantity == 0:
            price = bar["open"] * (1 + slip)
            budget = cash * body.allocation / 100
            size = budget / (price * (1 + fee))
            if body.market == "hk":
                size = math.floor(size / body.lot_size) * body.lot_size
            else:
                size = math.floor(size * 1e8) / 1e8
            if size > 0:
                entry_cost = size * price * (1 + fee)
                cash -= entry_cost
                quantity = size
                trades.append(dict(time=bar["time"], side="buy", price=price, quantity=size, fee=size * price * fee))
        elif pending == "sell" and quantity > 0:
            price = bar["open"] * (1 - slip)
            proceeds = quantity * price * (1 - fee)
            realized.append(proceeds - entry_cost)
            cash += proceeds
            trades.append(
                dict(
                    time=bar["time"],
                    side="sell",
                    price=price,
                    quantity=quantity,
                    fee=quantity * price * fee,
                    pnl=realized[-1],
                )
            )
            quantity = 0.0
        pending = None
        closes.append(bar["close"])
        equity = cash + quantity * bar["close"]
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
        curve.append(dict(time=bar["time"], value=equity))
        if len(closes) >= body.slow:
            bullish = sum(closes[-body.fast :]) / body.fast > sum(closes[-body.slow :]) / body.slow
            if previous is not None and bullish != previous:
                pending = "buy" if bullish else "sell"
            previous = bullish
    return dict(
        bars=bars,
        equity=curve,
        trades=trades,
        summary=dict(
            final_equity=curve[-1]["value"],
            return_pct=(curve[-1]["value"] / body.capital - 1) * 100,
            max_drawdown_pct=drawdown,
            closed_trades=len(realized),
            win_rate_pct=sum(x > 0 for x in realized) / len(realized) * 100 if realized else None,
            cash=cash,
            quantity=quantity,
            pending_signal=pending,
        ),
        parameters=body.model_dump(),
        method="均线交叉：收盘确认，下一根开盘模拟成交；只做多，含双边手续费和滑点；期末持仓按收盘价估值，不强制卖出。BTC 时间为 UTC，港股沿用香港行情时间。",
    )
