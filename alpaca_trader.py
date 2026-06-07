"""
Alpaca trading client — broker interface (connect, report, buy/sell).

This module is imported by run_daily.py for the scheduled job, and can also be
used standalone as a CLI for manual orders.

Setup
-----
1. Generate paper API keys at https://app.alpaca.markets/ (Paper account ->
   "Generate New Keys"). You get an API Key ID and a Secret Key.
2. Install the SDK:        pip install -r requirements.txt
3. Set credentials as environment variables (do NOT hardcode them):

   PowerShell (current session):
       $env:APCA_API_KEY_ID     = "your_key_id"
       $env:APCA_API_SECRET_KEY = "your_secret_key"

   PowerShell (persist for your user):
       setx APCA_API_KEY_ID     "your_key_id"
       setx APCA_API_SECRET_KEY "your_secret_key"

Usage (manual CLI)
------------------
   python alpaca_trader.py                 # show account + open positions
   python alpaca_trader.py AAPL 5          # buy 5 shares of AAPL (market)
   python alpaca_trader.py AAPL --notional 100   # buy $100 of AAPL (fractional)
   python alpaca_trader.py AAPL 5 --limit 190     # limit buy at $190

This defaults to the PAPER endpoint (no real money). Pass --live only if you
truly intend to trade real funds.
"""

import argparse
import os
import sys

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"  # sandbox, fake money


def get_client(paper: bool = True) -> TradingClient:
    """Build an authenticated Alpaca trading client from env vars."""
    key_id = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key_id or not secret:
        sys.exit(
            "Missing credentials. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
            "environment variables (see the setup notes at the top of this file)."
        )
    # alpaca-py picks the paper vs live host from the `paper` flag.
    return TradingClient(key_id, secret, paper=paper)


def show_account(client: TradingClient) -> None:
    acct = client.get_account()
    print(f"Account status : {acct.status}")
    print(f"Buying power   : ${float(acct.buying_power):,.2f}")
    print(f"Cash           : ${float(acct.cash):,.2f}")
    print(f"Portfolio value: ${float(acct.portfolio_value):,.2f}")
    if acct.trading_blocked:
        print("WARNING: trading is currently blocked on this account.")

    positions = client.get_all_positions()
    if positions:
        print("\nOpen positions:")
        for p in positions:
            print(
                f"  {p.symbol:<8} qty={p.qty:<10} "
                f"market_value=${float(p.market_value):,.2f} "
                f"unrealized_pl=${float(p.unrealized_pl):,.2f}"
            )
    else:
        print("\nNo open positions.")


def buy(
    client: TradingClient,
    symbol: str,
    qty: float | None = None,
    notional: float | None = None,
    limit_price: float | None = None,
) -> None:
    """Submit a buy order. Use either qty (shares) or notional (dollars)."""
    symbol = symbol.upper()
    common = dict(
        symbol=symbol,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    if qty is not None:
        common["qty"] = qty
    else:
        common["notional"] = notional

    if limit_price is not None:
        order_data = LimitOrderRequest(limit_price=limit_price, **common)
    else:
        order_data = MarketOrderRequest(**common)

    order = client.submit_order(order_data)
    size = f"{qty} shares" if qty is not None else f"${notional}"
    kind = f"limit @ {limit_price}" if limit_price else "market"
    print(f"Submitted {kind} BUY: {size} of {symbol}")
    print(f"  order id : {order.id}")
    print(f"  status   : {order.status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Buy assets via Alpaca.")
    parser.add_argument("symbol", nargs="?", help="Ticker, e.g. AAPL")
    parser.add_argument("qty", nargs="?", type=float, help="Number of shares")
    parser.add_argument("--notional", type=float, help="Dollar amount (fractional)")
    parser.add_argument("--limit", type=float, help="Limit price (omit for market order)")
    parser.add_argument("--live", action="store_true", help="Use REAL-money account")
    args = parser.parse_args()

    client = get_client(paper=not args.live)
    if args.live:
        print("*** LIVE TRADING — real money. ***")

    # No symbol -> just report account state.
    if not args.symbol:
        show_account(client)
        return

    if args.qty is None and args.notional is None:
        sys.exit("Specify a quantity (e.g. `AAPL 5`) or --notional (e.g. `AAPL --notional 100`).")
    if args.qty is not None and args.notional is not None:
        sys.exit("Use either qty or --notional, not both.")

    buy(
        client,
        args.symbol,
        qty=args.qty,
        notional=args.notional,
        limit_price=args.limit,
    )


if __name__ == "__main__":
    main()
