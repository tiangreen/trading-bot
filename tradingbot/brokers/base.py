"""Broker interface. Each venue implements these few methods."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Broker(ABC):
    @abstractmethod
    def daily_closes(self, symbols: list[str], bars: int) -> pd.DataFrame:
        """Adjusted daily closes of COMPLETED bars only, index = date, columns = symbols."""

    @abstractmethod
    def cash(self, currency: str) -> float:
        """Free cash in ``currency``."""

    @abstractmethod
    def positions(self, symbols: list[str]) -> dict[str, float]:
        """Current quantity held for each symbol (0 if none)."""

    @abstractmethod
    def round_qty(self, symbol: str, qty: float) -> float:
        """Round a positive quantity DOWN to the venue's tradable precision."""

    @abstractmethod
    def market_order(self, symbol: str, side: str, qty: float) -> str:
        """Submit a market order, return a short status/description string."""

    def close(self) -> None:  # optional
        pass
