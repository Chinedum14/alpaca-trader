# alpaca-trader

Automated daily trading on [Alpaca](https://alpaca.markets/), scheduled with
GitHub Actions. Defaults to the **paper** (fake-money) account.

## Layout

| File | Purpose |
|------|---------|
| `alpaca_trader.py` | Broker interface (connect, report account, place orders). Also a manual CLI. |
| `strategy.py` | **The scoring brain** — "The Omniscient Paradox" momentum rotation (see below). |
| `run_daily.py` | Scheduled entry point: report account → run strategy → place/log orders. |
| `.github/workflows/daily-trade.yml` | Cron schedule (weekdays, 3:55 PM ET) + manual trigger. |

## The strategy: "The Omniscient Paradox"

A tactical momentum strategy that, each day, scores 8 assets and rotates all
capital into the single best one (or hides in cash). It is implemented in
`strategy.py::generate_signals()`.

- **Universe (8):** `SOXL TECL TQQQ FAS ERX` (3x equity), `UUP TMF` (defensive),
  `BIL` (cash). **Mostly 3x leveraged ETFs — aggressive and high-risk. Paper only.**
- **Final Score** `= (Weighted Momentum / Volatility) × Trend Filter × RSI Penalty`
  - Momentum: rate-of-change over 9d (50%), 21d (30%), 63d (20%)
  - ÷ 21-day annualized volatility (reward smooth climbers)
  - × 0.5 if price is below its 50-day SMA
  - × 0.90 if RSI(14) is overbought (>85) or oversold (<30)
- **Regime filter:** if SPY is below its 200-day SMA → bear mode, only defensive
  assets (`UUP`/`TMF`/`BIL`) are eligible.
- **Selection:** hold the single best asset; only switch when a rival scores ≥10%
  higher. Bail to `BIL` if the held score drops below −0.02; only leave cash when
  the best score clears +0.02.
- **Position sizing:** `min(100%, 80% target vol ÷ asset's 20-day vol)`; the rest
  sits in `BIL`.
- **Drift rule:** only rebalances when a target weight moves more than 5 points,
  to avoid churn.
- **Execution:** runs at **3:55 PM ET** (5 min before close); liquidates exits
  fully before deploying cash into the new position.

> Daily-rebalanced 3x leveraged ETFs can lose value rapidly in choppy or falling
> markets. This is an educational reconstruction — keep `ALPACA_LIVE=0`.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env   # then edit .env with your paper keys
$env:APCA_API_KEY_ID     = "your_key_id"
$env:APCA_API_SECRET_KEY = "your_secret_key"

python run_daily.py           # dry-run-safe; places no orders until strategy.py returns signals
```

`run_daily.py` reads these env vars:

| Var | Default | Meaning |
|-----|---------|---------|
| `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` | — | Alpaca credentials (required) |
| `ALPACA_LIVE` | `0` | `1` = real money. Leave `0` for paper. |
| `DRY_RUN` | `0` | `1` = log intended orders without placing them. |
| `SKIP_IF_CLOSED` | `1` | `1` = no-op when the market is closed. |
| `ENFORCE_WINDOW` | `0` | `1` = only trade near `TRADE_WINDOW_ET` (set by the cron). |
| `TRADE_WINDOW_ET` | `15:55` | Target Eastern time to trade. |
| `TRADE_WINDOW_TOL_MIN` | `20` | Minutes of tolerance around the target. |
| `ALPACA_DATA_FEED` | `iex` | Bar feed (`iex` free; `sip` needs a subscription). |
| `INVEST_FRACTION` | `0.99` | Fraction of equity to deploy (cushion for fees/rounding). |

## Writing your strategy

Edit `generate_signals(client)` in `strategy.py`. Return a list of `Signal`
objects; return `[]` to do nothing. Example:

```python
def generate_signals(client):
    held = {p.symbol for p in client.get_all_positions()}
    if "SPY" not in held:
        return [Signal(symbol="SPY", notional=100)]   # buy $100 of SPY
    return []
```

For price/bar data to drive signals, use alpaca-py's data clients
(`from alpaca.data.historical import StockHistoricalDataClient`).

## Deploy to GitHub Actions

1. Create a **private** GitHub repo and push this folder (see below).
2. In the repo: **Settings → Secrets and variables → Actions → Secrets** →
   add `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` (your **paper** keys).
3. (Optional) Add a repo **Variable** `DRY_RUN = 1` while testing so runs place
   no real orders. Remove it (or set `0`) to go live on paper.
4. Run it once manually: **Actions → Daily Alpaca Trade → Run workflow**.

### Schedule notes / gotchas
- Cron is **UTC** and ignores US daylight saving. To hit **~3:55 PM ET** year-round
  the workflow fires **three staggered attempts** (3:40 / 3:48 / 3:55 ET), each
  listed in both UTC offsets — six cron lines total. `run_daily.py`'s
  `ENFORCE_WINDOW` guard (±20 min of 15:55 ET) lets only the correct-season
  attempts act; the rest self-skip. Manual runs bypass the window.
- GitHub's scheduler is **unreliable** — runs are often delayed and can be dropped
  under load. The staggered attempts are the mitigation: whichever lands first
  while the market is open does the trade, and later attempts see <5% drift and
  no-op, so they can't double-trade.
- GitHub shows run times in **your local timezone**, but cron fires on UTC. The
  trading run is `19:55 UTC` (summer) — e.g. 20:55 for a GMT+1 viewer, *not* the
  morning.
- Scheduled workflows are **auto-disabled after 60 days of no repo activity** —
  push a commit occasionally or re-enable in the Actions tab.
- Actions minutes are billable on private repos beyond the free monthly quota.

## Safety

- Credentials live only in env vars / GitHub Secrets — never commit them. `.env`
  is gitignored.
- Starts on the **paper** endpoint. Switching to live money requires setting
  `ALPACA_LIVE=1`; do that only deliberately.
