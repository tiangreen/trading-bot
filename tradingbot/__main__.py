"""Command line interface.

  python -m tradingbot backtest --sleeve stocks
  python -m tradingbot run                    # dry-run all sleeves (no orders sent)
  python -m tradingbot run --sleeve crypto --live
  python -m tradingbot status
  python -m tradingbot reset-killswitch --sleeve crypto
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from .config import load_config


def _setup_logging(state_dir: str) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(Path(state_dir) / "bot.log")],
    )


def cmd_backtest(cfg, args) -> int:
    from .backtest import run_backtest
    from .data import load_csv, load_history, synthetic_prices

    sleeve = cfg.sleeves[args.sleeve]
    if args.synthetic:
        freq = "D" if sleeve.strategy.annualization == 365 else "B"
        prices = synthetic_prices(sleeve.symbols, freq=freq)
    elif args.csv:
        prices = load_csv(args.csv)
    else:
        prices = load_history(sleeve.symbols, args.start)

    res = run_backtest(prices, sleeve)
    stats = res.stats(sleeve.strategy.annualization)
    print(f"\nBacktest '{sleeve.name}' {res.equity.index[0].date()} -> {res.equity.index[-1].date()}")
    print(f"{'metric':<20}{'strategy':>12}{'buy&hold(EW)':>14}")
    for k in ["total_return", "cagr", "volatility", "sharpe", "max_drawdown", "calmar"]:
        a, b = stats["strategy"][k], stats["buy_and_hold"][k]
        fmt = (lambda x: f"{x:.2f}") if k in ("sharpe", "calmar") else (lambda x: f"{x:.1%}")
        print(f"{k:<20}{fmt(a):>12}{fmt(b):>14}")
    print(f"{'avg_exposure':<20}{stats['strategy']['avg_exposure']:>12.1%}")
    print(f"{'turnover/year':<20}{stats['strategy']['turnover_per_year']:>12.2f}x")
    if args.plot:
        import matplotlib.pyplot as plt

        ax = res.equity.plot(label="strategy", figsize=(10, 5))
        res.benchmark.plot(ax=ax, label="buy & hold (equal weight)")
        ax.set_yscale("log")
        ax.legend()
        plt.savefig(args.plot, dpi=120, bbox_inches="tight")
        print(f"saved {args.plot}")
    return 0


def cmd_run(cfg, args) -> int:
    from .brokers import make_broker
    from .runner import run_sleeve
    from .state import StateStore

    store = StateStore(cfg.state_dir)
    names = [args.sleeve] if args.sleeve else list(cfg.sleeves)
    log = logging.getLogger("tradingbot")
    if not args.live:
        log.info("DRY-RUN: no orders will be sent (use --live to trade)")
    failed = False
    for name in names:
        sleeve = cfg.sleeves[name]
        broker = None
        try:
            broker = make_broker(sleeve.broker, cfg.brokers.get(sleeve.broker))
            run_sleeve(sleeve, broker, cfg.risk, store, date.today(), live=args.live,
                       force_rebalance=args.force_rebalance)
        except Exception:
            log.exception("[%s] run failed", name)
            failed = True
        finally:
            if broker is not None:
                broker.close()
    return 1 if failed else 0


def cmd_status(cfg, args) -> int:
    from .state import StateStore

    store = StateStore(cfg.state_dir)
    print(json.dumps({n: store.load(n).__dict__ for n in cfg.sleeves}, indent=2))
    return 0


def cmd_reset(cfg, args) -> int:
    from .state import StateStore

    store = StateStore(cfg.state_dir)
    st = store.load(args.sleeve)
    st.halted, st.halt_reason, st.peak_equity = False, "", 0.0
    store.save(args.sleeve, st)
    print(f"kill switch reset for '{args.sleeve}' (peak equity will be re-measured)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tradingbot")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="backtest one sleeve")
    b.add_argument("--sleeve", required=True)
    b.add_argument("--start", default="2015-01-01")
    b.add_argument("--csv", help="use a local CSV of closes instead of Yahoo")
    b.add_argument("--synthetic", action="store_true", help="offline demo on random data")
    b.add_argument("--plot", help="save equity curve PNG (needs matplotlib)")

    r = sub.add_parser("run", help="one trading cycle (dry-run unless --live)")
    r.add_argument("--sleeve")
    r.add_argument("--live", action="store_true", help="actually send orders")
    r.add_argument("--force-rebalance", action="store_true")

    sub.add_parser("status", help="show persisted state")
    k = sub.add_parser("reset-killswitch", help="re-enable a halted sleeve")
    k.add_argument("--sleeve", required=True)

    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    cfg = load_config(args.config)
    _setup_logging(cfg.state_dir)
    return {"backtest": cmd_backtest, "run": cmd_run, "status": cmd_status,
            "reset-killswitch": cmd_reset}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
