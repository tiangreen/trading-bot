"""Kraken (EU, MiCA-licensed) spot broker via ccxt.

Credentials come from the environment: KRAKEN_API_KEY / KRAKEN_API_SECRET.
Create the API key with "Query Funds", "Query Open Orders & Trades" and
"Create & Modify Orders" permissions only -- NEVER enable withdrawals.

Note: the bot treats the whole balance of each base asset (e.g. all BTC in the
account) as belonging to the sleeve. Use a dedicated (sub-)account.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pandas as pd

from .base import Broker


class KrakenBroker(Broker):
    def __init__(self, **_options):
        import ccxt  # lazy import

        self.ex = ccxt.kraken({
            "apiKey": os.environ.get("KRAKEN_API_KEY", ""),
            "secret": os.environ.get("KRAKEN_API_SECRET", ""),
            "enableRateLimit": True,
        })
        self.ex.load_markets()
        self._balance = None

    def _bal(self):
        if self._balance is None:
            self._balance = self.ex.fetch_balance()
        return self._balance

    def daily_closes(self, symbols, bars):
        since = int((time.time() - (bars + 5) * 86400) * 1000)
        today = datetime.now(timezone.utc).date()
        series = {}
        for s in symbols:
            ohlcv = self.ex.fetch_ohlcv(s, "1d", since=since)
            df = pd.DataFrame(ohlcv, columns=["ts", "o", "h", "l", "c", "v"])
            df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.date
            df = df[df["date"] < today]  # drop today's incomplete candle
            series[s] = df.set_index("date")["c"].astype(float)
        out = pd.DataFrame(series)
        out.index = pd.to_datetime(out.index)
        return out.sort_index().iloc[-bars:]

    def cash(self, currency):
        return float(self._bal()["free"].get(currency) or 0.0)

    def positions(self, symbols):
        total = self._bal()["total"]
        return {s: float(total.get(self.ex.market(s)["base"]) or 0.0) for s in symbols}

    def round_qty(self, symbol, qty):
        m = self.ex.market(symbol)
        min_amt = (m.get("limits", {}).get("amount", {}) or {}).get("min") or 0.0
        q = float(self.ex.amount_to_precision(symbol, qty))  # ccxt truncates by default
        return q if q >= min_amt else 0.0

    def market_order(self, symbol, side, qty):
        order = self.ex.create_order(symbol, "market", side, qty)
        self._balance = None
        return f"{order.get('status', 'submitted')} id={order.get('id')}"
