"""Build a self-contained HTML "mission control" dashboard for the bot.

Every number on the page comes from this repository's own code:

* ``run_backtest`` (same strategy / rebalance rules as live) -> equity, drawdown,
  exposure, weights, turnover, rebalance decisions.
* ``target_weights`` inputs (price vs. SMA, realised vol) -> trend state per asset.
* ``state/state.json`` and ``state/trades.csv`` (written by live/dry runs) ->
  kill-switch status and the real order log, when they exist.

    python -m tradingbot dashboard --synthetic          # offline demo data
    python -m tradingbot dashboard --start 2015-01-01   # Yahoo history
    open dashboard.html
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import run_backtest
from .config import Config, SleeveConfig

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
STRAND_LOOKBACKS = (20, 50, 100, 200)


def _r(x, nd=4):
    """Round floats (and lists of floats) for compact JSON; NaN/inf -> None."""
    if isinstance(x, (list, tuple, np.ndarray, pd.Series)):
        return [_r(v, nd) for v in x]
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def _drawdown_episodes(eq: pd.Series) -> list[dict]:
    dd = eq / eq.cummax() - 1
    out, start, trough = [], None, None
    for ts, v in dd.items():
        if v < 0 and start is None:
            start, trough = ts, ts
        if start is not None:
            if v < dd[trough]:
                trough = ts
            if v >= 0:
                out.append({"start": str(start.date()), "trough": str(trough.date()),
                            "end": str(ts.date()), "depth": _r(dd[trough]),
                            "days": (ts - start).days})
                start = None
    if start is not None:
        out.append({"start": str(start.date()), "trough": str(trough.date()), "end": None,
                    "depth": _r(dd[trough]), "days": (eq.index[-1] - start).days})
    return out


def sleeve_payload(prices: pd.DataFrame, sleeve: SleeveConfig, source: str,
                   state: dict | None, trades: list[dict]) -> dict:
    p = sleeve.strategy
    prices = prices[sleeve.symbols].sort_index().ffill()
    res = run_backtest(prices, sleeve)
    stats = res.stats(p.annualization)
    eq, bench = res.equity, res.benchmark.reindex(res.equity.index).ffill()
    px = prices.loc[eq.index[0]:]

    # weekly sampling keeps the file small; the last bar is always included
    wk = eq.resample("W").last().dropna().index
    wk = wk.map(lambda d: eq.index[eq.index <= d][-1]).unique()
    if wk[-1] != eq.index[-1]:
        wk = wk.append(pd.Index([eq.index[-1]]))

    # trend "strands": log(price / SMA_L) for several lookbacks, per asset
    strands = []
    for L in STRAND_LOOKBACKS:
        ratio = np.log(prices / prices.rolling(L, min_periods=L).mean()).reindex(wk)
        for s in sleeve.symbols:
            strands.append({"symbol": s, "lookback": L, "v": _r(ratio[s].fillna(0.0), 3)})

    sma = prices.rolling(p.sma_window, min_periods=p.sma_window).mean().iloc[-1]
    vol = prices.pct_change(fill_method=None).iloc[-p.vol_window:].std() * np.sqrt(p.annualization)
    last = prices.iloc[-1]
    w_last = res.weights.iloc[-1]

    # rebalance decisions the backtest actually made (days with turnover)
    dw = res.weights.diff().fillna(res.weights)
    decisions = []
    for ts in res.turnover[res.turnover > 1e-9].index[-40:]:
        for s in sleeve.symbols:
            d = float(dw.loc[ts, s])
            if abs(d) > 1e-6:
                decisions.append({"date": str(ts.date()), "symbol": s,
                                  "side": "buy" if d > 0 else "sell", "dw": _r(d),
                                  "w": _r(res.weights.loc[ts, s])})

    monthly = eq.resample("ME").last().pct_change().dropna()
    bmonthly = bench.resample("ME").last().pct_change().dropna()
    dd = eq / eq.cummax() - 1
    exposure = res.weights.sum(axis=1)

    return {
        "name": sleeve.name,
        "broker": sleeve.broker,
        "quote": sleeve.quote_currency,
        "source": source,
        "symbols": sleeve.symbols,
        "params": p.__dict__,
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "bars": len(eq),
        "dates": [str(d.date()) for d in wk],
        "equity": _r(eq.reindex(wk)),
        "bench": _r(bench.reindex(wk)),
        "drawdown": _r(dd.reindex(wk)),
        "exposure": _r(exposure.reindex(wk), 3),
        "weights": {s: _r(res.weights[s].reindex(wk), 3) for s in sleeve.symbols},
        "recent": {  # last 90 bars, daily
            "dates": [str(d.date()) for d in eq.index[-90:]],
            "exposure": _r(exposure.iloc[-90:], 3),
            "turnover": _r(res.turnover.loc[eq.index[-90:]], 4),
            "drawdown": _r(dd.iloc[-90:]),
        },
        "strands": strands,
        "assets": [{
            "symbol": s,
            "price": _r(last[s], 4),
            "sma": _r(sma[s], 4),
            "trend": _r(last[s] / sma[s] - 1) if np.isfinite(sma[s]) else None,
            "vol": _r(vol[s]),
            "weight": _r(w_last[s]),
            "avg_weight": _r(res.weights[s].mean()),
            "time_in_market": _r((res.weights[s] > 0).mean()),
        } for s in sleeve.symbols],
        "stats": {k: {m: _r(v) for m, v in d.items()} for k, d in stats.items()},
        "monthly": [{"m": d.strftime("%Y-%m"), "r": _r(v)} for d, v in monthly.items()],
        "bench_monthly": [_r(v) for v in bmonthly.values],
        "episodes": _drawdown_episodes(eq),
        "decisions": decisions[-30:],
        "state": state or {},
        "trades": [t for t in trades if t.get("sleeve") == sleeve.name][-30:],
    }


def _read_trades(state_dir: str) -> list[dict]:
    path = Path(state_dir) / "trades.csv"
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def build_dashboard(cfg: Config, prices_by_sleeve: dict[str, tuple[pd.DataFrame, str]],
                    out: str | Path, risk_note: dict | None = None) -> Path:
    store_path = Path(cfg.state_dir) / "state.json"
    state = json.loads(store_path.read_text()) if store_path.exists() else {}
    trades = _read_trades(cfg.state_dir)
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "risk": cfg.risk.__dict__,
        "sleeves": [sleeve_payload(px, cfg.sleeves[n], src, state.get(n), trades)
                    for n, (px, src) in prices_by_sleeve.items()],
    }
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", data)
    out = Path(out)
    out.write_text(html)
    return out
