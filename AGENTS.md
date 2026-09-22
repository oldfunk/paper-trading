# Paper Trading Framework — Agent Rules

## Project Overview

Local event-driven paper trading framework for A-share markets. Pure local operation, no third-party broker API required.

## Hard Rules

1. **Never fabricate market data** — always fetch via `AkshareFetcher` or read from local SQLite
2. **Never bypass risk checks** — all orders must go through `RiskManager`
3. **T+1 is mandatory** — `available_volume` must be 0 for same-day buys; `unfreeze_t1()` only runs on next settlement
4. **Costs are real** — commission (0.025%, min 5 CNY), stamp duty (0.05% sell-side), transfer fee (0.001%) must be deducted
5. **Slippage is configurable** — default 0.01 CNY fixed or 0.1% percentage
6. **Never claim "verified" without actual execution evidence** — manual step-by-step verification ≠ live verification

## Agent Interaction

### CLI Commands

```bash
# Check account status
python -m paper_trading.hermes_bridge status --json

# Run daily settlement
python -m paper_trading.hermes_bridge run --symbols 600519 000858 --json

# Place orders
python -m paper_trading.hermes_bridge buy --symbol 600519 --volume 100 --json
python -m paper_trading.hermes_bridge sell --symbol 600519 --volume 100 --price 1500.00 --json

# View history
python -m paper_trading.hermes_bridge nav --json
python -m paper_trading.hermes_bridge history --type orders --limit 20 --json
python -m paper_trading.hermes_bridge history --type fills --limit 20 --json
```

### Cron Integration

```bash
# Daily settlement at 4pm on weekdays
hermes cron add \
  --name "paper-trading-daily" \
  --schedule "0 16 * * 1-5" \
  --command "cd /path/to/paper-trading && python -m paper_trading.hermes_bridge cron-run --symbols 600519 000858 --json" \
  --no-agent
```

## Database

- `data.db` — market data (daily_bars, stock_pool)
- `paper_account.db` — account ledger (account, positions, orders, fills, nav_history, t1_freeze)

## Trading Rules

| Rule | Value |
|------|-------|
| Commission rate | 0.025% |
| Commission minimum | 5 CNY per order |
| Stamp duty | 0.05% (sell-side only) |
| Transfer fee | 0.001% |
| Slippage (fixed) | 0.01 CNY |
| Slippage (percentage) | 0.1% |
| T+1 freeze | Same-day buys unfrozen next day |

## Risk Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| max_single_order_value | 200,000 CNY | Max value per single order |
| max_position_pct | 30% | Max single-stock position |
| max_total_position_pct | 95% | Max total portfolio exposure |
| max_drawdown_pct | 20% | Max drawdown before halt |

## Strategy: MA Cross (MA5/MA20)

- **Golden cross** (MA5 crosses above MA20): BUY
- **Death cross** (MA5 crosses below MA20): SELL
- Default volume: 100 shares per signal

## Verification Checklist

After any trading operation, verify:

- [ ] Order status is `filled` or `rejected` (not `pending`)
- [ ] `available_volume` is 0 for same-day buys
- [ ] Commission >= 5 CNY when amount * 0.025% < 5
- [ ] Stamp duty is only on sells
- [ ] NAV history is recorded
- [ ] All state changes are persisted to SQLite

## Pi Deployment Sync

`~/paper-trading` on Pi is a git clone tracking `origin/main` (read-only deploy key).
Update ONLY via `git pull` — never scp/rsync files (that breaks LF endings and bypasses history).
`data.db` / `paper_account.db` / `venv/` are gitignored and survive pulls untouched.
