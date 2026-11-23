"""Daily backtester that uses exactly the same strategy and rebalance rules as live.

Timing (no look-ahead): the target computed from closes up to day t-1 is traded
at the close of day t. Costs = (fee_bps + slippage_bps) on traded notional.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import SleeveConfig
from .rebalance import decide_weights, period_key
from .strategy import target_weights


@dataclass
class BacktestResult:
    equity: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    benchmark: pd.Series

    def stats(self, annualization: int) -> dict[str, float]:
        return {
            "strategy": performance(self.equity, annualization)
            | {"avg_exposure": float(self.weights.sum(axis=1).mean()),
               "turnover_per_year": float(self.turnover.sum() / _years(self.equity))},
            "buy_and_hold": performance(self.benchmark, annualization),
        }


def _years(eq: pd.Series) -> float:
    return max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)


def performance(eq: pd.Series, annualization: int) -> dict[str, float]:
    rets = eq.pct_change().dropna()
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / _years(eq)) - 1
    vol = rets.std() * np.sqrt(annualization)
    dd = eq / eq.cummax() - 1
    max_dd = dd.min()
    return {
        "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
        "cagr": float(cagr),
        "volatility": float(vol),
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(annualization)) if rets.std() > 0 else 0.0,
        "max_drawdown": float(max_dd),
        "calmar": float(cagr / abs(max_dd)) if max_dd < 0 else float("inf"),
    }


def run_backtest(prices: pd.DataFrame, sleeve: SleeveConfig) -> BacktestResult:
    p = sleeve.strategy
    prices = prices[sleeve.symbols].sort_index().ffill()
    targets = target_weights(prices, p).shift(1).fillna(0.0)
    rets = prices.pct_change(fill_method=None).fillna(0.0)
    cost = (sleeve.fee_bps + sleeve.slippage_bps) / 10_000

    w = pd.Series(0.0, index=prices.columns)
    equity, last_period = 1.0, None
    eq_hist, w_hist, to_hist = [], [], []

    for ts in prices.index:
        r = rets.loc[ts]
        port_ret = float((w * r).sum())
        equity *= 1 + port_ret
        w = w * (1 + r) / (1 + port_ret) if port_ret != -1 else w * 0

        period = period_key(ts.date(), p.rebalance)
        new_w = decide_weights(w, targets.loc[ts], period != last_period, p.drift_threshold)
        if period != last_period:
            last_period = period
        turnover = float((new_w - w).abs().sum())
        equity *= 1 - turnover * cost
        w = new_w

        eq_hist.append(equity)
        w_hist.append(w.copy())
        to_hist.append(turnover)

    idx = prices.index
    first_valid = prices.apply(lambda s: s.first_valid_index()).max()
    bench_rets = rets.loc[first_valid:].mean(axis=1)  # equal-weight, daily-rebalanced buy & hold
    benchmark = (1 + bench_rets).cumprod()

    return BacktestResult(
        equity=pd.Series(eq_hist, index=idx).loc[first_valid:],
        weights=pd.DataFrame(w_hist, index=idx).loc[first_valid:],
        turnover=pd.Series(to_hist, index=idx).loc[first_valid:],
        benchmark=benchmark,
    )
