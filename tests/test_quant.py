import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest
from quant_engine import QuantRequest, simulate, crypto_bars
from paper_trading import PaperTrading
from workstation_store import Store


def candles(values):
    return [dict(time=(i + 1) * 3600, open=v, high=v + 1, low=v - 1, close=v, volume=10) for i, v in enumerate(values)]


def test_no_future_information_and_next_open_execution():
    bars = candles([10, 10, 9, 8, 12, 14, 16, 8, 7, 6])
    p = QuantRequest(fast=2, slow=3, fee_bps=0, slippage_bps=0)
    short = simulate(bars[:7], p)
    full = simulate(bars, p)
    assert short["equity"] == full["equity"][:7]
    trade = full["trades"][0]
    assert trade["time"] == bars[5]["time"]
    assert trade["price"] == bars[5]["open"]


def test_fees_slippage_lot_and_cash_limits():
    bars = candles([10, 10, 9, 8, 12, 14, 16, 8, 7, 6])
    p = QuantRequest(
        market="hk",
        symbol="HK.00700",
        fast=2,
        slow=3,
        capital=10000,
        allocation=100,
        lot_size=100,
        fee_bps=20,
        slippage_bps=10,
    )
    result = simulate(bars, p)
    assert all(t["quantity"] % 100 == 0 and t["fee"] > 0 for t in result["trades"])
    assert result["summary"]["cash"] >= 0
    free = simulate(bars, p.model_copy(update={"fee_bps": 0, "slippage_bps": 0}))
    assert result["summary"]["final_equity"] < free["summary"]["final_equity"]


def test_invalid_strategy_and_finite_parameters():
    for kwargs in ({"fast": 40, "slow": 20}, {"capital": float("nan")}, {"market": "btc", "symbol": "ETH/USDT"}):
        with pytest.raises(ValueError):
            QuantRequest(**kwargs)


def test_public_crypto_excludes_open_candle():
    import quant_engine

    quant_engine._cache.clear()
    closed = [3600000, 10, 11, 9, 10, 2]
    current = [7200000, 10, 11, 9, 10, 2]
    with patch("ccxt.okx") as exchange, patch("quant_engine.time.time", return_value=8000):
        exchange.return_value.fetch_ohlcv.side_effect = [[closed, current], []]
        result = crypto_bars(QuantRequest())
        assert exchange.call_args.args[0]["requests_trust_env"] is True
    assert len(result) == 1
    assert result[0]["time"] == 3600


def test_forward_paper_initialization_idempotence_and_pause():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "test.sqlite3")
        service = PaperTrading(store)
        service.configure(QuantRequest(fast=2, slow=3))
        bars = candles([10, 10, 9, 8, 12])
        with patch("paper_trading.get_bars", return_value=bars):
            service.tick()
            service.tick()
        assert service.state()["trades"] == []
        # Persisted state survives construction of a new service.
        assert PaperTrading(store).state()["last_time"] == bars[-1]["time"]
        with patch("paper_trading.get_bars", return_value=bars + candles([14, 16])):
            service.pause()
            service.tick()
        assert not service.state()["enabled"]


def test_paper_gap_does_not_backfill_trades():
    with tempfile.TemporaryDirectory() as tmp:
        service = PaperTrading(Store(Path(tmp) / "test.sqlite3"))
        service.configure(QuantRequest(fast=2, slow=3))
        bars = candles([10, 10, 9, 8, 12, 14, 16])
        with patch("paper_trading.get_bars", return_value=bars[:5]):
            service.tick()
        with patch("paper_trading.get_bars", return_value=bars):
            service.tick()
        assert not service.state()["trades"]
        assert "跳过" in service.state()["status"]


def test_forward_account_fills_only_the_next_new_bar():
    with tempfile.TemporaryDirectory() as tmp:
        service = PaperTrading(Store(Path(tmp) / "test.sqlite3"))
        service.configure(QuantRequest(fast=2, slow=3, fee_bps=0, slippage_bps=0))
        bars = candles([10, 10, 9, 8, 12, 14])
        with patch("paper_trading.get_bars", return_value=bars[:4]):
            service.tick()
        with patch("paper_trading.get_bars", return_value=bars[:5]):
            service.tick()
        assert service.state()["pending"] == "buy"
        assert not service.state()["trades"]
        with patch("paper_trading.get_bars", return_value=bars):
            service.tick()
            service.tick()
        assert len(service.state()["trades"]) == 1
        assert service.state()["trades"][0]["price"] == 14
