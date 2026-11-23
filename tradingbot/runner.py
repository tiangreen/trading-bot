"""One live/dry-run cycle for a sleeve. Designed to be run once a day by cron.

The runner is (almost) stateless: positions and cash are always read from the
broker, so restarts or crashes never leave the bot with a wrong view of the
account. Only the equity peak / kill switch / last rebalance period persist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .brokers.base import Broker
from .config import RiskConfig, SleeveConfig
from .rebalance import Order, decide_weights, period_key, plan_orders
from .risk import check_prices, drawdown_breached, limit_orders
from .state import StateStore
from .strategy import latest_target

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    equity: float
    target: pd.Series
    orders: list[Order] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    rebalanced: bool = False
    halted: bool = False


def run_sleeve(
    sleeve: SleeveConfig,
    broker: Broker,
    risk: RiskConfig,
    store: StateStore,
    today: date,
    live: bool = False,
    force_rebalance: bool = False,
) -> RunResult:
    p = sleeve.strategy
    syms = sleeve.symbols
    st = store.load(sleeve.name)

    bars = p.sma_window + p.vol_window + 10
    prices = broker.daily_closes(syms, bars)
    check_prices(prices, syms, today, risk)
    last = {s: float(prices[s].iloc[-1]) for s in syms}

    positions = broker.positions(syms)
    cash = broker.cash(sleeve.quote_currency)
    pos_value = sum(positions[s] * last[s] for s in syms)
    equity = min(cash + pos_value, sleeve.max_capital)
    if equity <= 0:
        raise RuntimeError(f"[{sleeve.name}] no equity available (cash={cash:.2f})")

    target = latest_target(prices, p)
    st.peak_equity = max(st.peak_equity, equity)

    if not st.halted and drawdown_breached(equity, st.peak_equity, risk):
        st.halted = True
        st.halt_reason = f"drawdown: equity {equity:.2f} vs peak {st.peak_equity:.2f}"
        log.error("[%s] KILL SWITCH: %s -> going flat", sleeve.name, st.halt_reason)
    if st.halted:
        target = target * 0.0

    period = period_key(today, p.rebalance)
    is_reb = force_rebalance or period != st.last_rebalance_period
    current_w = pd.Series({s: positions[s] * last[s] / equity for s in syms})
    new_w = decide_weights(current_w, target, is_reb, p.drift_threshold)

    orders = plan_orders(equity, min(cash, sleeve.max_capital), last, positions, new_w,
                         broker.round_qty, sleeve.min_order_value)
    orders = limit_orders(orders, risk, broker.round_qty)

    log.info("[%s] equity=%.2f cash=%.2f rebalance_day=%s halted=%s",
             sleeve.name, equity, cash, is_reb, st.halted)
    for s in syms:
        log.info("[%s]   %-10s price=%10.4f cur=%6.2f%% target=%6.2f%% new=%6.2f%%",
                 sleeve.name, s, last[s], 100 * current_w[s], 100 * target[s], 100 * new_w[s])

    results = []
    for o in orders:
        if live:
            try:
                r = broker.market_order(o.symbol, o.side, o.quantity)
            except Exception as e:  # keep going: one failing order must not block exits
                log.exception("[%s] order failed: %s", sleeve.name, o)
                r = f"ERROR {e}"
        else:
            r = "dry-run"
        log.info("[%s] %s %s %s (~%.2f %s) -> %s", sleeve.name, o.side.upper(), o.quantity,
                 o.symbol, o.value, sleeve.quote_currency, r)
        results.append(r)

    if orders:
        store.log_trades(sleeve.name, orders, not live, results)
    if live:  # dry-runs never mutate state
        if is_reb and not any(r.startswith("ERROR") for r in results):
            st.last_rebalance_period = period  # retry the rebalance next run if an order failed
        store.save(sleeve.name, st)

    return RunResult(equity, target, orders, results, is_reb, st.halted)
