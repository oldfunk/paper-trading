"""引擎守护进程入口：python -m paper_trading.engine

    python -m paper_trading.engine --interval 60            # 盘中守护（默认 60s）
    python -m paper_trading.engine --dry-run --once         # 单轮试运行，不下单
"""
from __future__ import annotations

import argparse
import json
import sys

from paper_trading.engine.runner import run_loop
from paper_trading.utils.run_lock import run_lock


def main() -> None:
    ap = argparse.ArgumentParser(description="规则引擎盘中守护（纸盘）")
    ap.add_argument("--data-db", default="data.db")
    ap.add_argument("--account-db", default="paper_account.db")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--secrets", default=None)
    ap.add_argument("--interval", type=int, default=0, help="轮询秒数（缺省读配置）")
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--dry-run", action="store_true", help="只判定不下单")
    ap.add_argument("--lock-file", default="paper_trading.lock")
    args = ap.parse_args()

    from paper_trading.cli import TradingBridge
    from paper_trading.engine.config import load_engine_config, validate

    bridge = TradingBridge(data_db=args.data_db, account_db=args.account_db,
                          config_path=args.config, secrets_path=args.secrets)
    cfg = load_engine_config(args.config)
    ok, msg = validate(cfg)
    print(f"规则引擎：{'启用' if cfg.get('enabled') else '未启用(enabled=false)'}｜"
          f"规则 {len(cfg.get('rules') or [])} 条｜{msg}")
    if not ok:
        sys.exit(2)
    if not cfg.get("enabled") and not args.once:
        print("enabled=false，拒绝启动守护（先在 config/engine.local.yaml 打开）")
        sys.exit(3)
    interval = args.interval or int(cfg.get("interval_sec") or 60)
    with run_lock(args.lock_file):
        out = run_loop(bridge, interval_sec=interval, dry_run=args.dry_run,
                       max_ticks=1 if args.once else 0)
    print(json.dumps(out[-1] if out else {}, ensure_ascii=False, indent=2,
                     default=str))


if __name__ == "__main__":
    main()