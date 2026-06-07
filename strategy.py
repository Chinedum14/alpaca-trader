"""
Strategy hook — decide what to trade on each run.

Fill in `generate_signals()` with your own logic. It receives an authenticated
Alpaca TradingClient (so you can read account state and positions) and must
return a list of `Signal` objects describing the orders you want placed.

Return an empty list to do nothing this run. run_daily.py turns each Signal
into an actual order (or logs it, when DRY_RUN is set).
"""
from __future__ import annotations

from dataclasses import dataclass

from alpaca.trading.client import TradingClient


@dataclass
class Signal:
    """One intended order."""
    symbol: str
    side: str = "buy"                  # "buy" or "sell"
    qty: float | None = None           # number of shares...
    notional: float | None = None      # ...OR a dollar amount (fractional). Use one.
    limit_price: float | None = None   # None -> market order


def generate_signals(client: TradingClient) -> list[Signal]:
    """Return the orders to place this run. Empty list = no trades.

    Replace the example body below with your real logic. You can inspect the
    account through `client`, e.g.:

        account = client.get_account()
        cash = float(account.cash)
        positions = {p.symbol: p for p in client.get_all_positions()}

    You can also pull market data with alpaca-py's data clients (see README)
    to compute signals.
    """
    signals: list[Signal] = []

    # --- EXAMPLE (placeholder — does nothing). Remove when you add real logic. ---
    # Buy $100 of SPY only if we don't already hold it:
    #
    #   positions = {p.symbol for p in client.get_all_positions()}
    #   if "SPY" not in positions:
    #       signals.append(Signal(symbol="SPY", notional=100))

    return signals
