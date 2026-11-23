"""Trend filter + volatility targeting.

For every day t, using only data up to and including the close of day t:

1. Trend filter: an asset is "on" if close > SMA(sma_window).
2. Inverse-volatility weights among the "on" assets (lower vol -> bigger weight).
3. Scale the whole basket so its estimated portfolio volatility (from the rolling
   covariance matrix) equals ``target_vol``; never exceed ``max_gross``.
4. Cap each asset at ``max_weight``. Anything not invested stays in cash.

The result is a DataFrame of target weights (fraction of sleeve equity).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyParams


def target_weights(prices: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    prices = prices.sort_index()
    rets = prices.pct_change(fill_method=None)
    sma = prices.rolling(p.sma_window, min_periods=p.sma_window).mean()
    trend_on = prices > sma  # NaN comparisons -> False
    vol = rets.rolling(p.vol_window, min_periods=p.vol_window).std() * np.sqrt(p.annualization)

    weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    values = rets.to_numpy()

    for i, ts in enumerate(prices.index):
        on = trend_on.iloc[i] & vol.iloc[i].notna() & (vol.iloc[i] > 0)
        if not on.any():
            continue
        cols = on[on].index
        inv_vol = 1.0 / vol.loc[ts, cols]
        w = inv_vol / inv_vol.sum()

        window = values[max(0, i - p.vol_window + 1): i + 1][:, [prices.columns.get_loc(c) for c in cols]]
        window = window[~np.isnan(window).any(axis=1)]
        if len(window) >= max(2, p.vol_window // 2):
            cov = np.cov(window, rowvar=False, ddof=1).reshape(len(cols), len(cols))
            port_vol = float(np.sqrt(w.to_numpy() @ cov @ w.to_numpy()) * np.sqrt(p.annualization))
        else:
            port_vol = float((w * vol.loc[ts, cols]).sum())  # conservative fallback: corr = 1

        if not np.isfinite(port_vol) or port_vol <= 0:
            continue
        scale = min(p.target_vol / port_vol, p.max_gross)
        w = (w * scale).clip(upper=p.max_weight)
        weights.loc[ts, cols] = w.to_numpy()

    return weights


def latest_target(prices: pd.DataFrame, p: StrategyParams) -> pd.Series:
    """Target weights based on the most recent completed bar."""
    return target_weights(prices, p).iloc[-1]
