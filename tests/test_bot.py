from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest import run_backtest
from tradingbot.brokers.paper import PaperBroker
from tradingbot.config import RiskConfig, SleeveConfig, StrategyParams, load_config
from tradingbot.data import synthetic_prices
from tradingbot.rebalance import decide_weights, period_key, plan_orders
from tradingbot.risk import RiskError, check_prices, limit_orders
from tradingbot.rebalance import Order
from tradingbot.runner import run_sleeve
from tradingbot.state import StateStore
from tradingbot.strategy import target_weights

SYMS = ["AAA", "BBB", "CCC"]


def params(**kw):
    base = dict(sma_window=50, vol_window=20, target_vol=0.12, max_weight=0.4)
    base.update(kw)
    return StrategyParams(**base)


def sleeve(**kw):
    base = dict(name="test", broker="paper", quote_currency="USD", max_capital=100_000,
                symbols=SYMS, strategy=params(), min_order_value=10)
    base.update(kw)
    return SleeveConfig(**base)


@pytest.fixture
def prices():
    return synthetic_prices(SYMS, days=600, seed=1)


# ---------------- strategy ----------------

def test_no_lookahead(prices):
    w1 = target_weights(prices, params())
    future = prices.copy()
    future.iloc[400:] *= np.linspace(0.3, 3, len(future) - 400)[:, None]  # change the future
    w2 = target_weights(future, params())
    pd.testing.assert_frame_equal(w1.iloc[:400], w2.iloc[:400])


def test_weights_respect_limits(prices):
    p = params(target_vol=5.0)  # absurd target -> must be capped
    w = target_weights(prices, p)
    assert (w >= 0).all().all()
    assert (w <= p.max_weight + 1e-12).all().all()
    assert (w.sum(axis=1) <= p.max_gross + 1e-9).all()


def test_zero_weight_below_trend():
    idx = pd.date_range("2020-01-01", periods=300, freq="B")
    falling = pd.DataFrame({"X": np.linspace(200, 100, 300) * (1 + 0.01 * np.sin(np.arange(300)))}, index=idx)
    w = target_weights(falling, params())
    assert w["X"].iloc[-1] == 0.0
    assert (w.iloc[:50] == 0).all().all()  # not enough history -> flat


def test_vol_target_is_hit_approximately(prices):
    p = params(target_vol=0.10, max_weight=1.0)
    w = target_weights(prices, p)
    rets = prices.pct_change().fillna(0)
    invested = w.shift(1).sum(axis=1) > 0.5
    realised = (w.shift(1) * rets).sum(axis=1)[invested].std() * np.sqrt(252)
    assert 0.06 < realised < 0.15


# ---------------- rebalance ----------------

def test_decide_weights_exit_any_day_entry_only_on_rebalance():
    cur = pd.Series({"A": 0.3, "B": 0.0, "C": 0.20})
    tgt = pd.Series({"A": 0.0, "B": 0.25, "C": 0.22})
    off = decide_weights(cur, tgt, is_rebalance_day=False, drift_threshold=0.05)
    assert off.to_dict() == {"A": 0.0, "B": 0.0, "C": 0.20}
    on = decide_weights(cur, tgt, is_rebalance_day=True, drift_threshold=0.05)
    assert on.to_dict() == {"A": 0.0, "B": 0.25, "C": 0.20}  # C within drift band


def test_plan_orders_sells_first_and_respects_cash():
    rq = lambda s, q: float(int(q))
    orders = plan_orders(
        equity=10_000, cash=1_000, prices={"A": 100.0, "B": 50.0},
        positions={"A": 50.0, "B": 0.0}, new_weights=pd.Series({"A": 0.0, "B": 0.9}),
        round_qty=rq, min_order_value=10,
    )
    assert orders[0].side == "sell" and orders[0].quantity == 50
    buy = orders[1]
    assert buy.side == "buy" and 5_000 < buy.value <= (1_000 + 5_000) * 0.995


def test_period_key():
    assert period_key(date(2026, 11, 23), "weekly") == period_key(date(2026, 11, 27), "weekly")
    assert period_key(date(2026, 11, 23), "weekly") != period_key(date(2026, 11, 30), "weekly")
    assert period_key(date(2026, 11, 23), "monthly") == "2026-11"


# ---------------- risk ----------------

def test_stale_data_rejected(prices):
    with pytest.raises(RiskError):
        check_prices(prices, SYMS, prices.index[-1].date().replace(year=2099), RiskConfig())


def test_buy_orders_clipped_sells_not():
    rq = lambda s, q: float(int(q))
    cfg = RiskConfig(max_order_value=1_000)
    out = limit_orders([Order("A", "buy", 100, 50.0), Order("B", "sell", 100, 50.0)], cfg, rq)
    assert out[0].quantity == 20 and out[1].quantity == 100


# ---------------- backtest ----------------

def test_backtest_runs_and_costs_hurt(prices):
    cheap = run_backtest(prices, sleeve(fee_bps=0, slippage_bps=0))
    pricey = run_backtest(prices, sleeve(fee_bps=50, slippage_bps=50))
    assert cheap.equity.iloc[-1] > pricey.equity.iloc[-1]
    assert cheap.weights.sum(axis=1).max() <= 1.0 + 1e-6
    stats = cheap.stats(252)
    assert stats["strategy"]["max_drawdown"] > stats["buy_and_hold"]["max_drawdown"]  # shallower DD


# ---------------- runner (end-to-end with paper broker) ----------------

def test_runner_end_to_end(prices, tmp_path):
    broker = PaperBroker(prices, cash=100_000)
    store = StateStore(tmp_path)
    today = prices.index[-1].date()
    s = sleeve()

    dry = run_sleeve(s, broker, RiskConfig(max_order_value=1e9), store, today, live=False)
    assert broker.fills == [] and dry.rebalanced

    live = run_sleeve(s, broker, RiskConfig(max_order_value=1e9), store, today, live=True)
    assert len(broker.fills) == len(live.orders) > 0
    assert broker.cash("USD") >= 0
    assert store.load("test").last_rebalance_period == period_key(today, "weekly")

    again = run_sleeve(s, broker, RiskConfig(max_order_value=1e9), store, today, live=True)
    assert not again.rebalanced and again.orders == []  # already rebalanced this week


def test_kill_switch_goes_flat(prices, tmp_path):
    broker = PaperBroker(prices, cash=100_000)
    store = StateStore(tmp_path)
    today = prices.index[-1].date()
    run_sleeve(sleeve(), broker, RiskConfig(max_order_value=1e9), store, today, live=True, force_rebalance=True)
    assert any(broker.holdings.values())

    st = store.load("test")
    st.peak_equity = 1e9  # simulate a huge drawdown
    store.save("test", st)
    res = run_sleeve(sleeve(), broker, RiskConfig(max_order_value=1e9), store, today, live=True)
    assert res.halted
    assert all(q == 0 for q in broker.holdings.values())


def test_example_config_loads():
    cfg = load_config("config.example.yaml")
    assert set(cfg.sleeves) == {"stocks", "crypto"}
    assert cfg.sleeves["crypto"].strategy.annualization == 365
