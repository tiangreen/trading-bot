"""In-memory broker for tests and offline simulation."""

from __future__ import annotations

import math

import pandas as pd

from .base import Broker


class PaperBroker(Broker):
    def __init__(self, prices: pd.DataFrame, cash: float, currency: str = "USD",
                 fractional: bool = False, fee_rate: float = 0.0):
        self.prices = prices
        self._cash = {currency: cash}
        self.currency = currency
        self.holdings: dict[str, float] = {}
        self.fractional = fractional
        self.fee_rate = fee_rate
        self.fills: list[tuple[str, str, float, float]] = []

    def daily_closes(self, symbols, bars):
        return self.prices[symbols].iloc[-bars:]

    def cash(self, currency):
        return self._cash.get(currency, 0.0)

    def positions(self, symbols):
        return {s: self.holdings.get(s, 0.0) for s in symbols}

    def round_qty(self, symbol, qty):
        return math.floor(qty * 1e6) / 1e6 if self.fractional else float(math.floor(qty))

    def market_order(self, symbol, side, qty):
        price = float(self.prices[symbol].iloc[-1])
        value = qty * price
        fee = value * self.fee_rate
        if side == "buy":
            self._cash[self.currency] -= value + fee
            self.holdings[symbol] = self.holdings.get(symbol, 0.0) + qty
        else:
            if qty > self.holdings.get(symbol, 0.0) + 1e-12:
                raise ValueError(f"cannot sell {qty} {symbol}, only hold {self.holdings.get(symbol, 0.0)}")
            self._cash[self.currency] += value - fee
            self.holdings[symbol] -= qty
        self.fills.append((symbol, side, qty, price))
        return f"filled {qty} @ {price:.4f}"
