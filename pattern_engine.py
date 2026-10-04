"""Snapshot rules, not order execution. All calculations use observed samples only."""

import math
import uuid
from collections import defaultdict, deque
from typing import List, Literal
from pydantic import BaseModel, Field, model_validator, ConfigDict


class Pattern(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    name: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    kind: Literal["drop", "volume", "breakout"]
    window_sec: int = Field(default=600, ge=60, le=7200)
    threshold: float = Field(gt=0, le=1e12)
    min_price: float = Field(default=0, ge=0, le=1000000)
    min_turnover: float = Field(default=0, ge=0, le=1e13)
    cooldown_sec: int = Field(default=600, ge=60, le=86400)
    min_samples: int = Field(default=3, ge=2, le=1000)
    warmup_sec: int = Field(default=30, ge=0, le=7200)
    recovery_ratio: float = Field(default=0.7, gt=0, lt=1)
    scope: Literal["all", "watchlist"] = "all"
    mobile: bool = True

    @model_validator(mode="after")
    def validate_rule(self):
        if self.kind in ("drop", "breakout") and self.threshold > 100:
            raise ValueError("价格阈值以百分数填写，最大100")
        if self.warmup_sec >= self.window_sec:
            raise ValueError("预热时间必须小于窗口")
        return self


DEFAULT_PATTERNS = [
    dict(id="risk-drop", name="急跌风险", kind="drop", window_sec=600, threshold=15),
    dict(id="volume-surge", name="区间放量", kind="volume", window_sec=1800, threshold=5000000),
    dict(
        id="breakout",
        name="向上突破",
        kind="breakout",
        window_sec=600,
        threshold=1,
        enabled=False,
        min_turnover=3000000,
    ),
]


def max_drawdown(samples):
    peak = 0
    best = 0
    peak_at = low_at = None
    best_peak = best_low = 0
    for ts, price, vol in samples:
        if price > peak:
            peak = price
            peak_at = ts
        drop = (peak - price) / peak if peak else 0
        if drop > best:
            best = drop
            low_at = ts
            best_peak = peak
            best_low = price
            best_peak_at = peak_at
    return best * 100, best_peak, best_low


class PatternEngine:
    def __init__(self):
        self.samples = defaultdict(deque)
        self.flags = {}
        self.fingerprint = None

    def reset(self):
        self.samples.clear()
        self.flags.clear()

    def evaluate(self, row, now, timestamp, patterns, watchlist):
        signature = tuple(str(p.model_dump()) for p in patterns)
        if signature != self.fingerprint:
            self.reset()
            self.fingerprint = signature
        code = str(row["code"])
        price = float(row["last_price"])
        volume = float(row["volume"])
        if (
            not all(map(math.isfinite, [price, volume]))
            or price <= 0
            or volume < 0
            or bool(row.get("suspension", False))
        ):
            return []
        active = [p for p in patterns if p.enabled and (p.scope == "all" or code in watchlist)]
        if not active:
            return []
        q = self.samples[code]
        if q and volume < q[-1][2]:
            q.clear()
            for p in active:
                self.flags.pop((code, p.id), None)
        q.append((now, price, volume))
        window = max(p.window_sec for p in active)
        while q and q[0][0] < now - window:
            q.popleft()
        alerts = []
        for p in active:
            if price < p.min_price or float(row.get("turnover", 0)) < p.min_turnover:
                continue
            samples = [s for s in q if s[0] >= now - p.window_sec]
            if len(samples) < p.min_samples or now - samples[0][0] < p.warmup_sec:
                continue
            if p.kind == "drop":
                value, peak, low = max_drawdown(samples)
                detail = f"观测窗口最大回撤 {value:.2f}%；高点 {peak:.3f}，低点 {low:.3f}"
            elif p.kind == "volume":
                value = max(0, volume - samples[0][2])
                detail = f"观测窗口增量成交 {value:,.0f} 股（累计量差值）"
            else:
                peak = max(s[1] for s in samples[:-1])
                value = (price / peak - 1) * 100
                detail = f"突破此前窗口高点 {peak:.3f}，幅度 {value:.2f}%"
            key = (code, p.id)
            armed, last = self.flags.get(key, (True, -1e20))
            if not armed and value < p.threshold * p.recovery_ratio:
                armed = True
            if armed and value >= p.threshold and now - last >= p.cooldown_sec:
                detail += (
                    f"\n配置窗口 {p.window_sec // 60} 分钟；已观测 {int(now - samples[0][0])} 秒；现价 {price:.3f}"
                )
                name = str(row.get("name", code))
                alerts.append(
                    dict(
                        id=uuid.uuid4().hex,
                        timestamp=timestamp,
                        time=timestamp[11:19],
                        epoch=now,
                        code=code,
                        name=name,
                        type=p.name,
                        title=f"【{p.name}】{code} {name}",
                        detail=detail,
                        price=price,
                        pattern_id=p.id,
                        pattern=p.model_dump(),
                        metric=value,
                        mobile=p.mobile,
                    )
                )
                armed = False
                last = now
            self.flags[key] = (armed, last)
        return alerts
