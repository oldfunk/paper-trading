"""策略模块。"""
from .base_strategy import BaseStrategy
from .ma_cross_strategy import MACrossStrategy
from .schemes import (
    active_name,
    all_schemes,
    builtin_schemes,
    custom_schemes,
    mother_strategies,
    resolve_scheme,
    set_active,
)

__all__ = ["BaseStrategy", "MACrossStrategy", "active_name", "all_schemes",
           "builtin_schemes", "custom_schemes", "mother_strategies",
           "resolve_scheme", "set_active"]
