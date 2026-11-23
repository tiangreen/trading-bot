"""Small persistent state: equity peak, kill-switch flag, last rebalance period, trade log."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .rebalance import Order


@dataclass
class SleeveState:
    peak_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    last_rebalance_period: str = ""


class StateStore:
    def __init__(self, state_dir: str | Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"

    def _read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def load(self, sleeve: str) -> SleeveState:
        return SleeveState(**self._read().get(sleeve, {}))

    def save(self, sleeve: str, st: SleeveState) -> None:
        data = self._read()
        data[sleeve] = asdict(st)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)  # atomic write

    def log_trades(self, sleeve: str, orders: list[Order], dry_run: bool, results: list[str]) -> None:
        path = self.dir / "trades.csv"
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "sleeve", "symbol", "side", "quantity", "est_price", "dry_run", "result"])
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for o, r in zip(orders, results):
                w.writerow([now, sleeve, o.symbol, o.side, o.quantity, o.est_price, dry_run, r])
