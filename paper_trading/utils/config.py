"""配置加载：config.yaml 单一真相源。"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        raise RuntimeError("Need pyyaml: pip install pyyaml (for config.yaml)")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """加载配置并填充默认值。"""
    from paper_trading.models import TradingConfig

    raw = _read_yaml(path)
    trading = raw.get("trading", {})
    account = raw.get("account", {})
    strategy = raw.get("strategy", {})
    risk = raw.get("risk", {})

    tcfg = TradingConfig(
        commission_rate=float(trading.get("commission_rate", 0.00025)),
        commission_min=float(trading.get("commission_min", 5.0)),
        stamp_duty_rate=float(trading.get("stamp_duty_rate", 0.0005)),
        transfer_fee_rate=float(trading.get("transfer_fee_rate", 0.00001)),
        slippage_fixed=float(trading.get("slippage_fixed", 0.01)),
        slippage_pct=float(trading.get("slippage_pct", 0.001)),
        use_slippage_pct=bool(trading.get("use_slippage_pct", False)),
        initial_cash=float(account.get("initial_cash", 1_000_000.0)),
        holidays=tuple(trading.get("holidays", []) or ()),
    )
    return {
        "raw": raw,
        "trading_config": tcfg,
        "strategy": {
            "short_window": int(strategy.get("short_window", 5)),
            "long_window": int(strategy.get("long_window", 20)),
            "buy_volume": int(strategy.get("buy_volume", 100)),
            "sell_volume": int(strategy.get("sell_volume", 100)),
        },
        "risk": {
            "max_single_order_value": float(risk.get("max_single_order_value", 200_000.0)),
            "max_position_pct": float(risk.get("max_position_pct", 0.3)),
            "max_total_position_pct": float(risk.get("max_total_position_pct", 0.95)),
            "max_drawdown_pct": float(risk.get("max_drawdown_pct", 0.20)),
        },
        "stock_pool": list(raw.get("stock_pool", [])),
    }
