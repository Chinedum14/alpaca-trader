"""
Daily scheduled entry point.

Builds an Alpaca client, reports the account, asks the strategy for signals,
and places the resulting orders (unless DRY_RUN is set). Designed to be run
unattended by GitHub Actions, but also runs fine locally.

Environment variables
----------------------
  APCA_API_KEY_ID, APCA_API_SECRET_KEY   (required) — Alpaca API credentials
  ALPACA_LIVE     = "1" to trade real money            (default: paper)
  DRY_RUN         = "1" to log intended orders only     (default: place them)
  SKIP_IF_CLOSED  = "0" to trade even when market closed (default: skip)
"""
import os

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from alpaca_trader import get_client, show_account
from strategy import Signal, generate_signals


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def place(client, sig: Signal, dry_run: bool) -> None:
    """Translate a Signal into an Alpaca order (or log it under dry-run)."""
    side = OrderSide.SELL if sig.side.lower() == "sell" else OrderSide.BUY
    common = dict(symbol=sig.symbol.upper(), side=side, time_in_force=TimeInForce.DAY)

    if sig.qty is not None:
        common["qty"] = sig.qty
    elif sig.notional is not None:
        common["notional"] = sig.notional
    else:
        print(f"  SKIP {sig.symbol}: signal has neither qty nor notional")
        return

    if sig.limit_price is not None:
        order_data = LimitOrderRequest(limit_price=sig.limit_price, **common)
    else:
        order_data = MarketOrderRequest(**common)

    size = f"{sig.qty} shares" if sig.qty is not None else f"${sig.notional}"
    kind = f"limit @ {sig.limit_price}" if sig.limit_price else "market"

    if dry_run:
        print(f"  DRY-RUN {sig.side.upper()} {kind}: {size} of {sig.symbol.upper()}")
        return

    order = client.submit_order(order_data)
    print(
        f"  {sig.side.upper()} {kind}: {size} of {sig.symbol.upper()} "
        f"-> id={order.id} status={order.status}"
    )


def main() -> None:
    live = _env_flag("ALPACA_LIVE")
    dry_run = _env_flag("DRY_RUN")
    skip_if_closed = _env_flag("SKIP_IF_CLOSED", default=True)

    mode = "LIVE" if live else "PAPER"
    if dry_run:
        mode += " / DRY-RUN"
    print(f"=== Alpaca daily run ({mode}) ===")

    client = get_client(paper=not live)
    show_account(client)

    if skip_if_closed:
        clock = client.get_clock()
        if not clock.is_open:
            print(f"\nMarket is closed (next open: {clock.next_open}). Skipping trades.")
            return

    print("\nGenerating signals...")
    signals = generate_signals(client)
    if not signals:
        print("No signals this run. Nothing to do.")
        return

    print(f"{len(signals)} signal(s) to place:")
    for sig in signals:
        place(client, sig, dry_run)


if __name__ == "__main__":
    main()
