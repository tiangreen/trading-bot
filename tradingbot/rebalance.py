"""Turn target weights into concrete orders.

Shared by the backtester and the live runner so both follow identical rules:

* Exits (target weight 0 while holding) are executed on ANY day.
* Entries / resizing only happen on rebalance days, and only when the
  difference between target and current weight exceeds ``drift_threshold``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd


def period_key(d: date, frequency: str) -> str:
    if frequency == "daily":
        return d.isoformat()
    if frequency == "weekly":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if frequency == "monthly":
        return f"{d.year}-{d.month:02d}"
    raise ValueError(frequency)


def decide_weights(
    current: pd.Series,
    target: pd.Series,
    is_rebalance_day: bool,
    drift_threshold: float,
) -> pd.Series:
    current = current.reindex(target.index).fillna(0.0)
    new = current.copy()
    for sym in target.index:
        tgt, cur = float(target[sym]), float(current[sym])
        if tgt == 0.0 and cur != 0.0:
            new[sym] = 0.0  # trend broken -> exit immediately
        elif is_rebalance_day and abs(tgt - cur) > drift_threshold:
            new[sym] = tgt
    return new


@dataclass
class Order:
    symbol: str
    side: str        # "buy" | "sell"
    quantity: float
    est_price: float

    @property
    def value(self) -> float:
        return self.quantity * self.est_price


def plan_orders(
    equity: float,
    cash: float,
    prices: dict[str, float],
    positions: dict[str, float],
    new_weights: pd.Series,
    round_qty: Callable[[str, float], float],
    min_order_value: float,
) -> list[Order]:
    """Compute orders moving ``positions`` to ``new_weights * equity``.

    Sells come first; buys are scaled down if they would exceed available cash.
    ``round_qty`` must round a (positive) quantity DOWN to the venue's precision.
    """
    sells, buys = [], []
    for sym, w in new_weights.items():
        price = prices[sym]
        held = positions.get(sym, 0.0)
        if w == 0.0:
            if held > 0:
                sells.append(Order(sym, "sell", held, price))  # full exit, exact quantity
            continue
        delta = w * equity / price - held
        qty = round_qty(sym, abs(delta))
        if qty <= 0 or qty * price < min_order_value:
            continue
        (buys if delta > 0 else sells).append(Order(sym, "buy" if delta > 0 else "sell", qty, price))

    budget = cash + sum(o.value for o in sells)
    wanted = sum(o.value for o in buys)
    if wanted > budget and wanted > 0:
        factor = max(budget, 0.0) * 0.995 / wanted  # keep a small buffer for fees/price moves
        scaled = []
        for o in buys:
            qty = round_qty(o.symbol, o.quantity * factor)
            if qty > 0 and qty * o.est_price >= min_order_value:
                scaled.append(Order(o.symbol, "buy", qty, o.est_price))
        buys = scaled
    return sells + buys
