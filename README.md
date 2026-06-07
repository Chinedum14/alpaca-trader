# alpaca-trader

Automated daily trading on [Alpaca](https://alpaca.markets/), scheduled with
GitHub Actions. Defaults to the **paper** (fake-money) account.

## Layout

| File | Purpose |
|------|---------|
| `alpaca_trader.py` | Broker interface (connect, report account, place orders). Also a manual CLI. |
| `strategy.py` | **Your logic.** Fill in `generate_signals()` to decide what to trade. |
| `run_daily.py` | Scheduled entry point: report account → run strategy → place/log orders. |
| `.github/workflows/daily-trade.yml` | Cron schedule (weekdays) + manual trigger. |

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
- Cron is **UTC** and ignores US daylight saving. `30 14 * * 1-5` ≈ 9:30 ET in
  winter / 10:30 ET in summer. Edit the cron in the workflow to taste.
- GitHub may **delay** scheduled runs by several minutes under load.
- Scheduled workflows are **auto-disabled after 60 days of no repo activity** —
  push a commit occasionally or re-enable in the Actions tab.
- Actions minutes are billable on private repos beyond the free monthly quota.

## Safety

- Credentials live only in env vars / GitHub Secrets — never commit them. `.env`
  is gitignored.
- Starts on the **paper** endpoint. Switching to live money requires setting
  `ALPACA_LIVE=1`; do that only deliberately.
