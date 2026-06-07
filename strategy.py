"""
Strategy: "The Omniscient Paradox" — a tactical momentum rotation system.

This is the asset-scoring brain referenced by run_daily.py. Each run it scores a
fixed universe of 8 assets, rotates capital into the single best one (subject to
turnover/regime guards), sizes the position by volatility targeting, and returns
the orders needed to reach that target. run_daily.py executes the orders.

Reconstructed faithfully from the strategy specification:

  Final Score = (Weighted Momentum / Volatility) x Trend Filter x RSI Penalty

  Universe ........ 7 risky ETFs (SOXL TECL TQQQ FAS ERX UUP TMF) + BIL (cash)
  Momentum ........ ROC over 9d (50%), 21d (30%), 63d (20%)
  Risk adjust ..... divide weighted momentum by 21-day annualized volatility
  Trend filter .... price < 50-day SMA  -> x0.5  (else x1.0)
  RSI penalty ..... RSI(14) > 85 or < 30 -> x0.90 (else x1.0)
  Regime filter ... SPY < 200-day SMA -> bear: only defensive assets (UUP/TMF/BIL)
  Selection ....... hold single best asset; switch only if >=10% better
                    (emergency floor -0.02 -> BIL; leave cash only if best > 0.02)
  Position size ... min(100%, 80% target vol / 20-day asset vol); rest in BIL
  Drift ........... only rebalance when a target weight moves more than 5 points

WARNING: the risky universe is mostly 3x leveraged ETFs. This is an aggressive,
high-risk design intended for the PAPER account. Do not point it at real money
without understanding the drawdown profile of daily-rebalanced 3x leverage.
"""
from __future__ import annotations

import datetime as dt
import math
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient

# --- Universe -------------------------------------------------------------
CASH = "BIL"                                              # safe asset / cash
EQUITY_3X = ["SOXL", "TECL", "TQQQ", "FAS", "ERX"]       # aggressive 3x equity
DEFENSIVE = ["UUP", "TMF"]                                # flight-to-safety
RISKY = EQUITY_3X + DEFENSIVE                             # the 7 scored ETFs
UNIVERSE = RISKY + [CASH]
BENCHMARK = "SPY"                                         # regime thermometer

# --- Scoring / selection constants (from the spec) ------------------------
MOM_WEIGHTS = {9: 0.50, 21: 0.30, 63: 0.20}              # ROC lookback -> weight
VOL_SCORE_DAYS = 21                                       # risk-adjust denominator
VOL_SIZE_DAYS = 20                                        # position-sizing vol
SMA_TREND = 50                                            # per-asset trend filter
SMA_REGIME = 200                                          # SPY bull/bear line
RSI_PERIOD = 14
RSI_HIGH, RSI_LOW = 85.0, 30.0
TREND_PENALTY = 0.5                                       # below 50-day SMA
RSI_PENALTY = 0.90                                        # RSI in an extreme zone
TRADING_DAYS = 252                                        # annualization factor

TARGET_VOL = 0.80                                         # 80% annualized
MAX_POSITION = 1.00                                       # never lever past 100%
CONFIDENCE = 1.10                                         # new must be >=10% better
EMERGENCY_FLOOR = -0.02                                   # held score below -> cash
ENTRY_THRESHOLD = 0.02                                    # leave cash only if above
DRIFT_TOLERANCE = 0.05                                    # 5% minimum drift to trade

# Deploy slightly under 100% so rounding/fees never trip "insufficient buying
# power" when we rebuild the book. Override with INVEST_FRACTION if desired.
INVEST_FRACTION = float(os.environ.get("INVEST_FRACTION", "0.99"))
MIN_ORDER_USD = 1.0                                       # skip dust orders


@dataclass
class Signal:
    """One intended order."""
    symbol: str
    side: str = "buy"                  # "buy" or "sell"
    qty: float | None = None           # number of shares...
    notional: float | None = None      # ...OR a dollar amount (fractional). Use one.
    limit_price: float | None = None   # None -> market order


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------
def _data_client() -> StockHistoricalDataClient:
    """Historical-data client, built from the same Alpaca credentials."""
    return StockHistoricalDataClient(
        os.environ.get("APCA_API_KEY_ID"),
        os.environ.get("APCA_API_SECRET_KEY"),
    )


def _fetch_closes(symbols: list[str]) -> dict[str, pd.Series]:
    """Return {symbol: close Series} of split/dividend-adjusted daily bars.

    Uses the IEX feed by default (free on paper accounts). We pull ~400 calendar
    days so the 200-day SMA always has enough history.
    """
    feed = DataFeed.SIP if os.environ.get("ALPACA_DATA_FEED", "iex").lower() == "sip" else DataFeed.IEX
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)  # avoid last-minute partial bar
    start = end - dt.timedelta(days=400)

    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        adjustment=Adjustment.ALL,   # adjust for splits/dividends -> clean indicators
        feed=feed,
    )
    bars = _data_client().get_stock_bars(req)
    df = bars.df
    out: dict[str, pd.Series] = {}
    if df is None or df.empty:
        return out
    for sym in symbols:
        try:
            closes = df.loc[sym]["close"].sort_index()
        except KeyError:
            continue  # symbol returned no data this run
        if len(closes) >= 2:
            out[sym] = closes.astype(float)
    return out


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------
def _roc(close: pd.Series, n: int) -> float | None:
    """Rate of change over n trading days, as a fraction (0.05 = +5%)."""
    if len(close) <= n:
        return None
    past = close.iloc[-1 - n]
    if past == 0:
        return None
    return float(close.iloc[-1] / past - 1.0)


def _annualized_vol(close: pd.Series, n: int) -> float | None:
    """Annualized realized volatility from the last n daily returns."""
    returns = close.pct_change().dropna()
    if len(returns) < n:
        return None
    sd = returns.tail(n).std(ddof=1)
    if not np.isfinite(sd) or sd == 0:
        return None
    return float(sd * math.sqrt(TRADING_DAYS))


def _sma(close: pd.Series, n: int) -> float | None:
    if len(close) < n:
        return None
    return float(close.tail(n).mean())


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> float | None:
    """Wilder's RSI of the latest bar (0-100)."""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def _score(close: pd.Series) -> float | None:
    """Compute the Final Score for one asset, or None if data is insufficient."""
    weighted_mom = 0.0
    for days, weight in MOM_WEIGHTS.items():
        roc = _roc(close, days)
        if roc is None:
            return None
        weighted_mom += weight * roc

    vol = _annualized_vol(close, VOL_SCORE_DAYS)
    if vol is None:
        return None
    risk_adjusted = weighted_mom / vol

    sma50 = _sma(close, SMA_TREND)
    trend = 1.0 if (sma50 is not None and close.iloc[-1] > sma50) else TREND_PENALTY

    rsi = _rsi(close)
    penalty = RSI_PENALTY if (rsi is not None and (rsi > RSI_HIGH or rsi < RSI_LOW)) else 1.0

    return risk_adjusted * trend * penalty


# --------------------------------------------------------------------------
# Strategy decision
# --------------------------------------------------------------------------
def _current_holding(positions: dict[str, float]) -> str | None:
    """The risky asset we currently hold (largest non-cash position), or None."""
    risky_held = {s: v for s, v in positions.items() if s in RISKY and v > 0}
    if not risky_held:
        return None
    return max(risky_held, key=risky_held.get)


def _choose_target(
    scores: dict[str, float],
    regime_bull: bool,
    current: str | None,
) -> str | None:
    """Pick the target risky asset (or None = sit in cash/BIL).

    Encodes the spec's selection logic: regime filter, emergency floor, the
    positive-momentum entry threshold, and the 10% confidence switch rule.
    """
    candidates = RISKY if regime_bull else DEFENSIVE
    scored = {s: scores[s] for s in candidates if s in scores}
    if not scored:
        return None
    best = max(scored, key=scored.get)
    best_score = scored[best]
    current_score = scores.get(current) if current else None

    # Emergency floor: bail to cash if what we hold has gone clearly negative.
    if current is not None and current_score is not None and current_score < EMERGENCY_FLOOR:
        return None

    # Bear regime forces de-risking out of any 3x equity we may still hold.
    if not regime_bull and current in EQUITY_3X:
        return best if best_score > ENTRY_THRESHOLD else None

    # Currently in cash: only deploy if the best asset clears the entry bar.
    if current is None or current not in scores:
        return best if best_score > ENTRY_THRESHOLD else None

    # Holding a still-eligible asset: stay unless a rival is decisively better.
    if best == current:
        return current
    if best_score > ENTRY_THRESHOLD and best_score >= CONFIDENCE * max(current_score, 0.0):
        return best
    return current


def _position_size(vol20: float | None) -> float:
    """Volatility-targeted weight for the chosen asset, capped at 100%."""
    if vol20 is None or vol20 <= 0:
        return MAX_POSITION
    return max(0.0, min(MAX_POSITION, TARGET_VOL / vol20))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def generate_signals(client: TradingClient) -> list[Signal]:
    """Score the universe, decide the target book, and return orders to reach it."""
    closes = _fetch_closes(UNIVERSE + [BENCHMARK])

    # --- Regime: SPY vs its 200-day SMA. Default to bear (defensive) if unknown.
    spy = closes.get(BENCHMARK)
    spy_sma = _sma(spy, SMA_REGIME) if spy is not None else None
    regime_bull = bool(spy is not None and spy_sma is not None and spy.iloc[-1] > spy_sma)
    print(f"  Regime: {'BULL (SPY > 200d SMA)' if regime_bull else 'BEAR / defensive'}")

    # --- Score every risky asset.
    scores: dict[str, float] = {}
    for sym in RISKY:
        s = _score(closes[sym]) if sym in closes else None
        if s is not None:
            scores[sym] = s
    if not scores:
        print("  No scorable assets (insufficient market data). Holding.")
        return []

    print("  Scores:")
    for sym, sc in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        print(f"    {sym:<5} {sc:+.4f}")

    # --- Current book.
    account = client.get_account()
    portfolio_value = float(account.portfolio_value)
    positions = {p.symbol: float(p.market_value) for p in client.get_all_positions()}
    qty_held = {p.symbol: float(p.qty) for p in client.get_all_positions()}
    current = _current_holding(positions)
    print(f"  Currently holding: {current or 'cash/BIL'}  (portfolio ${portfolio_value:,.2f})")

    # --- Decide the target asset and its size.
    target = _choose_target(scores, regime_bull, current)
    target_weights: dict[str, float] = {}
    if target is None:
        target_weights = {CASH: 1.0}
        print("  Target: 100% BIL (cash).")
    else:
        size = _position_size(_annualized_vol(closes[target], VOL_SIZE_DAYS))
        target_weights[target] = size
        if size < 1.0:
            target_weights[CASH] = 1.0 - size
        print(f"  Target: {size:.0%} {target}" + (f" + {1 - size:.0%} BIL" if size < 1.0 else ""))

    # --- Drift gate: only trade if some target weight moved more than 5 points.
    current_weights = {s: v / portfolio_value for s, v in positions.items()} if portfolio_value > 0 else {}
    symbols = set(target_weights) | set(current_weights)
    max_drift = max((abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in symbols), default=0.0)
    if max_drift <= DRIFT_TOLERANCE:
        print(f"  Max drift {max_drift:.1%} <= {DRIFT_TOLERANCE:.0%} tolerance. No rebalance needed.")
        return []
    print(f"  Max drift {max_drift:.1%} > {DRIFT_TOLERANCE:.0%}. Rebalancing.")

    # --- Build orders: liquidate exits fully, then size up/down the rest.
    # Sells are emitted first so run_daily can clear them before buying.
    sells: list[Signal] = []
    buys: list[Signal] = []
    target_dollars = {s: w * portfolio_value * INVEST_FRACTION for s, w in target_weights.items()}

    for sym in symbols:
        cur = positions.get(sym, 0.0)
        tgt = target_dollars.get(sym, 0.0)

        if tgt <= 0.0 and sym in qty_held and qty_held[sym] > 0:
            sells.append(Signal(symbol=sym, side="sell", qty=qty_held[sym]))  # clean liquidation
            continue

        delta = tgt - cur
        if abs(delta) < MIN_ORDER_USD:
            continue
        if delta < 0:
            sells.append(Signal(symbol=sym, side="sell", notional=round(-delta, 2)))
        else:
            buys.append(Signal(symbol=sym, side="buy", notional=round(delta, 2)))

    return sells + buys
