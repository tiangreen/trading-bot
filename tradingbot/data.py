"""Historical data for backtests (Yahoo Finance, cached as CSV) + synthetic data for offline tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def yahoo_symbol(symbol: str) -> str:
    # "BTC/EUR" (ccxt) -> "BTC-EUR" (Yahoo); "BRK.B" -> "BRK-B"
    return symbol.replace("/", "-").replace(".", "-")


def load_history(symbols: list[str], start: str, cache_dir: str | Path = "data") -> pd.DataFrame:
    import yfinance as yf  # lazy import

    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    cols = {}
    for s in symbols:
        f = cache / f"{yahoo_symbol(s)}.csv"
        if f.exists():
            ser = pd.read_csv(f, index_col=0, parse_dates=True).iloc[:, 0]
        else:
            df = yf.download(yahoo_symbol(s), start=start, auto_adjust=True, progress=False)
            if df.empty:
                raise RuntimeError(f"no data from Yahoo for {s}")
            ser = df["Close"].squeeze()
            ser.to_frame("close").to_csv(f)
        cols[s] = ser.loc[start:]
    out = pd.DataFrame(cols)
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out.sort_index()


def load_csv(path: str | Path) -> pd.DataFrame:
    """CSV with a date column first and one close-price column per symbol."""
    return pd.read_csv(path, index_col=0, parse_dates=True).sort_index()


def synthetic_prices(symbols: list[str], days: int = 2500, seed: int = 7,
                     annual_vol: float = 0.25, freq: str = "B") -> pd.DataFrame:
    """Correlated random walks with regime changes (bull/bear) -- for offline demos and tests."""
    rng = np.random.default_rng(seed)
    n = len(symbols)
    corr = np.full((n, n), 0.5) + 0.5 * np.eye(n)
    chol = np.linalg.cholesky(corr)
    daily_vol = annual_vol / np.sqrt(252)
    regime = np.where((np.arange(days) // 400) % 3 == 2, -0.0008, 0.0006)  # every 3rd block is a bear market
    shocks = rng.standard_normal((days, n)) @ chol.T * daily_vol
    vols = np.linspace(0.7, 1.5, n)
    rets = regime[:, None] + shocks * vols
    prices = 100 * np.exp(np.cumsum(rets, axis=0))
    idx = pd.date_range("2015-01-01", periods=days, freq=freq)
    return pd.DataFrame(prices, index=idx, columns=symbols)
