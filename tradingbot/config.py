"""Configuration loading (YAML -> dataclasses)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class StrategyParams:
    sma_window: int = 200          # trend filter: price must be above its N-day SMA
    vol_window: int = 60           # lookback for realised volatility / covariance
    target_vol: float = 0.12       # annualised volatility target for the sleeve
    max_weight: float = 0.20       # max weight per single asset
    max_gross: float = 1.0         # max total exposure (1.0 = no leverage)
    annualization: int = 252       # 252 for stocks, 365 for crypto
    rebalance: str = "weekly"      # daily | weekly | monthly
    drift_threshold: float = 0.05  # only rebalance a position if |target - current| > this


@dataclass
class SleeveConfig:
    name: str
    broker: str                    # ibkr | kraken | paper
    quote_currency: str            # USD for US stocks, EUR for Kraken EUR pairs
    max_capital: float             # bot never manages more than this (in quote currency)
    symbols: list[str]
    strategy: StrategyParams
    min_order_value: float = 50.0
    fee_bps: float = 5.0           # backtest only: commission per side, basis points
    slippage_bps: float = 5.0      # backtest only: slippage per side, basis points


@dataclass
class RiskConfig:
    max_drawdown_kill: float = 0.25   # halt sleeve & go flat if equity falls this far from peak
    max_order_value: float = 5_000.0  # clip any single order to this notional
    max_orders_per_run: int = 20
    max_price_age_days: int = 5       # refuse to trade on stale data


@dataclass
class Config:
    sleeves: dict[str, SleeveConfig]
    risk: RiskConfig = field(default_factory=RiskConfig)
    brokers: dict[str, dict[str, Any]] = field(default_factory=dict)
    state_dir: str = "state"


def _strategy(raw: dict[str, Any] | None) -> StrategyParams:
    params = StrategyParams(**(raw or {}))
    if params.rebalance not in {"daily", "weekly", "monthly"}:
        raise ValueError(f"invalid rebalance frequency: {params.rebalance}")
    return params


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    sleeves = {}
    for name, s in raw["sleeves"].items():
        s = dict(s)
        strategy = _strategy(s.pop("strategy", None))
        sleeves[name] = SleeveConfig(name=name, strategy=strategy, **s)
    return Config(
        sleeves=sleeves,
        risk=RiskConfig(**(raw.get("risk") or {})),
        brokers=raw.get("brokers") or {},
        state_dir=raw.get("state_dir", "state"),
    )
