"""Interactive Brokers (IBKR Ireland / Central Europe for German residents) via ib_async.

Requires a running TWS or IB Gateway with the API enabled:
  * paper account port: 7497 (TWS) / 4002 (Gateway)
  * live account port:  7496 (TWS) / 4001 (Gateway)

The bot manages US stocks with USD cash in the account. Convert EUR->USD
yourself in IBKR (or enable auto FX) so that the USD cash balance is funded.
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .base import Broker


class IBKRBroker(Broker):
    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 17,
                 order_timeout: int = 120, **_options):
        from ib_async import IB  # lazy import

        self.ib = IB()
        self.ib.connect(host, port, clientId=client_id, timeout=20)
        self.order_timeout = order_timeout
        self._contracts: dict = {}

    def _contract(self, symbol: str):
        if symbol not in self._contracts:
            from ib_async import Stock

            c = Stock(symbol, "SMART", "USD")
            self.ib.qualifyContracts(c)
            self._contracts[symbol] = c
        return self._contracts[symbol]

    def daily_closes(self, symbols, bars):
        years = math.ceil(bars / 250) + 1
        today_ny = datetime.now(ZoneInfo("America/New_York")).date()
        series = {}
        for s in symbols:
            data = self.ib.reqHistoricalData(
                self._contract(s), endDateTime="", durationStr=f"{years} Y",
                barSizeSetting="1 day", whatToShow="ADJUSTED_LAST", useRTH=True,
            )
            series[s] = pd.Series({b.date: float(b.close) for b in data if b.date < today_ny})
        out = pd.DataFrame(series)
        out.index = pd.to_datetime(out.index)
        return out.sort_index().iloc[-bars:]

    def cash(self, currency):
        for v in self.ib.accountValues():
            if v.tag == "CashBalance" and v.currency == currency:
                return float(v.value)
        return 0.0

    def positions(self, symbols):
        held = {p.contract.symbol: float(p.position) for p in self.ib.positions()
                if p.contract.secType == "STK"}
        return {s: held.get(s, 0.0) for s in symbols}

    def round_qty(self, symbol, qty):
        return float(math.floor(qty))  # whole shares

    def market_order(self, symbol, side, qty):
        from ib_async import MarketOrder

        trade = self.ib.placeOrder(self._contract(symbol), MarketOrder(side.upper(), qty, tif="DAY"))
        waited = 0.0
        while not trade.isDone() and waited < self.order_timeout:
            self.ib.sleep(1)
            waited += 1
        st = trade.orderStatus
        return f"{st.status} filled={st.filled} avg={st.avgFillPrice}"

    def close(self):
        self.ib.disconnect()
