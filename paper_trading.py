"""Persisted forward paper account; never submits exchange/broker orders."""

import threading
import time
import math
from quant_engine import QuantRequest, get_bars


class PaperTrading:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None

    def state(self):
        with self.lock:
            return self.store.get("paper_account", {"enabled": False, "status": "尚未建立模拟账户", "trades": []})

    def configure(self, body):
        if body.end:
            raise ValueError("持续模拟请留空截止日期")
        with self.lock:
            previous = self.state()
            if previous.get("enabled"):
                raise ValueError("请先暂停原账户，再建立新模拟账户")
            self.store.put(
                "paper_account",
                dict(
                    enabled=True,
                    parameters=body.model_dump(),
                    cash=body.capital,
                    quantity=0.0,
                    last_time=None,
                    pending=None,
                    previous=None,
                    trades=[],
                    status="等待首轮已收盘行情",
                    equity=body.capital,
                ),
            )
        return self.state()

    def pause(self):
        with self.lock:
            data = self.state()
            data.update(enabled=False, status="已暂停；持仓保留，仅模拟")
            self.store.put("paper_account", data)
        return data

    def start(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.worker, daemon=True, name="paper-trading")
        self.thread.start()

    def tick(self):
        with self.lock:
            state = self.state()
        if not state.get("enabled"):
            return
        body = QuantRequest(**state["parameters"])
        bars = get_bars(body)
        with self.lock:
            if self.state() != state:
                return  # configuration changed while fetching
            if len(bars) < body.slow + 1:
                raise ValueError("模拟策略样本不足")
            last = bars[-1]
            closes = [b["close"] for b in bars]
            bullish = sum(closes[-body.fast :]) / body.fast > sum(closes[-body.slow :]) / body.slow
            if state["last_time"] is None:
                state.update(last_time=last["time"], previous=bullish, status="模拟账户已预热，等待新的均线交叉")
            elif last["time"] > state["last_time"]:
                new = [b for b in bars if b["time"] > state["last_time"]]
                if len(new) != 1:
                    # A gap must not silently become fictitious retrospective live fills.
                    state.update(
                        last_time=last["time"],
                        previous=bullish,
                        pending=None,
                        status="行情跨过多根K线，跳过断线区间并重新预热",
                    )
                else:
                    fee = body.fee_bps / 10000
                    slip = body.slippage_bps / 10000
                    side = state["pending"]
                    if side == "buy" and state["quantity"] == 0:
                        price = last["open"] * (1 + slip)
                        quantity = state["cash"] * body.allocation / 100 / (price * (1 + fee))
                        quantity = (
                            math.floor(quantity / body.lot_size) * body.lot_size
                            if body.market == "hk"
                            else math.floor(quantity * 1e8) / 1e8
                        )
                        if quantity > 0:
                            state["cash"] -= quantity * price * (1 + fee)
                            state["quantity"] = quantity
                            state["trades"].append(
                                dict(
                                    time=last["time"],
                                    side="buy",
                                    price=price,
                                    quantity=quantity,
                                    fee=quantity * price * fee,
                                )
                            )
                    elif side == "sell" and state["quantity"] > 0:
                        price = last["open"] * (1 - slip)
                        quantity = state["quantity"]
                        state["cash"] += quantity * price * (1 - fee)
                        state["quantity"] = 0.0
                        state["trades"].append(
                            dict(
                                time=last["time"],
                                side="sell",
                                price=price,
                                quantity=quantity,
                                fee=quantity * price * fee,
                            )
                        )
                    state.update(
                        last_time=last["time"],
                        pending=("buy" if bullish else "sell") if bullish != state["previous"] else None,
                        previous=bullish,
                        status="持续模拟中；仅记录已收盘K线后的模拟成交",
                    )
            state["equity"] = state["cash"] + state["quantity"] * last["close"]
            state["trades"] = state["trades"][-500:]
            state["checked_at"] = int(time.time())
            self.store.put("paper_account", state)

    def worker(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                with self.lock:
                    state = self.state()
                    if state.get("enabled"):
                        state["status"] = "行情暂不可用，模拟未成交；稍后重试"
                        self.store.put("paper_account", state)
            self.stop_event.wait(30)
