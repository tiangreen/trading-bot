"""Pre-trade risk checks. Every order passes through here before execution."""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Callable

import pandas as pd

from .config import RiskConfig
from .rebalance import Order

log = logging.getLogger(__name__)


class RiskError(RuntimeError):
    """Raised when it is unsafe to trade at all (bad/stale data, etc.)."""


def check_prices(prices: pd.DataFrame, symbols: list[str], today: date, cfg: RiskConfig) -> None:
    missing = [s for s in symbols if s not in prices.columns]
    if missing:
        raise RiskError(f"no price data for {missing}")
    last_ts = pd.Timestamp(prices.index[-1]).date()
    age = (today - last_ts).days
    if age > cfg.max_price_age_days:
        raise RiskError(f"price data is stale: last bar {last_ts}, {age} days old")
    last = prices.iloc[-1][symbols]
    bad = [s for s in symbols if not (isinstance(last[s], float) and math.isfinite(last[s]) and last[s] > 0)]
    if bad:
        raise RiskError(f"invalid latest prices for {bad}: {last[bad].to_dict()}")


def drawdown_breached(equity: float, peak: float, cfg: RiskConfig) -> bool:
    return peak > 0 and equity < peak * (1 - cfg.max_drawdown_kill)


def limit_orders(
    orders: list[Order], cfg: RiskConfig, round_qty: Callable[[str, float], float]
) -> list[Order]:
    out: list[Order] = []
    for o in orders:
        if o.value > cfg.max_order_value and o.side == "buy":
            qty = round_qty(o.symbol, cfg.max_order_value / o.est_price)
            log.warning("clipping %s %s from %.2f to %.2f (max_order_value)", o.side, o.symbol, o.quantity, qty)
            if qty <= 0:
                continue
            o = Order(o.symbol, o.side, qty, o.est_price)
        out.append(o)
    # Sells are never clipped: reducing risk should not be blocked by a size limit.
    if len(out) > cfg.max_orders_per_run:
        raise RiskError(f"{len(out)} orders exceeds max_orders_per_run={cfg.max_orders_per_run}")
    return out
