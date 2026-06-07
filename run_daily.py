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
  ENFORCE_WINDOW  = "1" to only trade near TRADE_WINDOW_ET (default: off)
  TRADE_WINDOW_ET = "HH:MM" Eastern target time          (default: 15:55)
  TRADE_WINDOW_TOL_MIN = minutes of tolerance around it   (default: 20)
"""
import datetime as dt
import os
import time
from zoneinfo import ZoneInfo

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from alpaca_trader import get_client, show_account
from strategy import Signal, generate_signals

EASTERN = ZoneInfo("America/New_York")


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def within_trade_window() -> tuple[bool, dt.datetime]:
    """True if the current Eastern time is within tolerance of the target time.

    Lets us schedule the GitHub cron at both possible UTC times (EST and EDT)
    and only actually trade at ~3:55 PM ET, sidestepping daylight-saving drift.
    """
    now_et = dt.datetime.now(EASTERN)
    hh, mm = (int(x) for x in os.environ.get("TRADE_WINDOW_ET", "15:55").split(":"))
    tol = int(os.environ.get("TRADE_WINDOW_TOL_MIN", "20"))
    target = now_et.replace(hour=hh, minute=mm, second=0, microsecond=0)
    diff_min = abs((now_et - target).total_seconds()) / 60.0
    return diff_min <= tol, now_et


def place(client, sig: Signal, dry_run: bool):
    """Translate a Signal into an Alpaca order (or log it under dry-run).

    Returns the submitted order (so callers can wait for fills), or None.
    """
    side = OrderSide.SELL if sig.side.lower() == "sell" else OrderSide.BUY
    common = dict(symbol=sig.symbol.upper(), side=side, time_in_force=TimeInForce.DAY)

    if sig.qty is not None:
        common["qty"] = sig.qty
    elif sig.notional is not None:
        common["notional"] = sig.notional
    else:
        print(f"  SKIP {sig.symbol}: signal has neither qty nor notional")
        return None

    if sig.limit_price is not None:
        order_data = LimitOrderRequest(limit_price=sig.limit_price, **common)
    else:
        order_data = MarketOrderRequest(**common)

    size = f"{sig.qty} shares" if sig.qty is not None else f"${sig.notional}"
    kind = f"limit @ {sig.limit_price}" if sig.limit_price else "market"

    if dry_run:
        print(f"  DRY-RUN {sig.side.upper()} {kind}: {size} of {sig.symbol.upper()}")
        return None

    order = client.submit_order(order_data)
    print(
        f"  {sig.side.upper()} {kind}: {size} of {sig.symbol.upper()} "
        f"-> id={order.id} status={order.status}"
    )
    return order


def wait_for_fills(client, orders, timeout: float = 60.0) -> None:
    """Block until the given orders reach a terminal state (or timeout).

    Used to fully liquidate exits before deploying cash into new positions,
    per the strategy's 'clean slate' execution discipline.
    """
    terminal = {"filled", "canceled", "expired", "rejected", "done_for_day"}
    ids = [o.id for o in orders if o is not None]
    deadline = time.monotonic() + timeout
    while ids and time.monotonic() < deadline:
        time.sleep(2)
        still_open = []
        for oid in ids:
            status = str(client.get_order_by_id(oid).status).split(".")[-1].lower()
            if status not in terminal:
                still_open.append(oid)
        ids = still_open
    if ids:
        print(f"  WARNING: {len(ids)} sell order(s) not confirmed filled before buying.")


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

    if _env_flag("ENFORCE_WINDOW"):
        ok, now_et = within_trade_window()
        print(f"\nEastern time now: {now_et:%Y-%m-%d %H:%M} ET")
        if not ok:
            print("Outside the trade window. Skipping (this is the wrong-season cron firing).")
            return

    print("\nGenerating signals...")
    signals = generate_signals(client)
    if not signals:
        print("No signals this run. Nothing to do.")
        return

    sells = [s for s in signals if s.side.lower() == "sell"]
    buys = [s for s in signals if s.side.lower() != "sell"]
    print(f"{len(signals)} signal(s) to place ({len(sells)} sell, {len(buys)} buy):")

    # Liquidate first, let those fills settle, then deploy the freed cash.
    placed = [place(client, sig, dry_run) for sig in sells]
    if buys and not dry_run and any(o is not None for o in placed):
        print("  Waiting for sells to settle before buying...")
        wait_for_fills(client, placed)
    for sig in buys:
        place(client, sig, dry_run)


if __name__ == "__main__":
    main()
