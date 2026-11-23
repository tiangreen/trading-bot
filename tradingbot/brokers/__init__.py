from .base import Broker


def make_broker(name: str, options: dict | None = None) -> Broker:
    options = options or {}
    if name == "ibkr":
        from .ibkr import IBKRBroker
        return IBKRBroker(**options)
    if name == "kraken":
        from .kraken import KrakenBroker
        return KrakenBroker(**options)
    raise ValueError(f"unknown broker: {name}")


__all__ = ["Broker", "make_broker"]
