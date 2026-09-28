"""投资方案（Scheme）：打法的一等公民。

- 内置方案随代码走（版本化）；用户自定义 + 当前选中只写 gitignored 的
  `strategy.local.yaml`，git 树永远干净
- 母项目策略（成长/红利/困境反转）是选股侧的语言，本方案引用它只做
  universe 来源（screening 按 strategy_tag 过滤），执行规则归本方案管：
  母管“买什么”，本方案管“怎么买卖”
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

LOCAL_FILE = "strategy.local.yaml"


@dataclass
class Scheme:
    name: str
    title: str = ""
    desc: str = ""
    source: str = "builtin"  # builtin | custom | mother
    available: bool = True  # mother 类需检测到母项目才可用
    universe_source: str = "config"  # config | watchlist | screening | all
    universe_tag: str = ""  # screening 按 strategy_tag 过滤（如 growth）
    universe_limit: int = 20
    signal: dict = field(default_factory=dict)  # short_window/long_window/buy_volume/sell_volume
    sizing: dict = field(default_factory=dict)  # max_orders_per_run/max_order_value
    risk: dict = field(default_factory=dict)  # daily_loss_halt_pct
    allow_buy: bool = True
    exits_note: str = ""  # 离场纪律（v1 进 prompt，硬止损以后单做）


def builtin_schemes() -> dict[str, Scheme]:
    """内置三套（默认行为 = ma_trend，与现状零差异）。"""
    return {
        "ma_trend": Scheme(
            name="ma_trend", title="趋势跟踪",
            desc="MA5/20 金叉买入、死叉卖出；配置池内决策，偏积极",
            universe_source="config",
            signal={"short_window": 5, "long_window": 20,
                    "buy_volume": 100, "sell_volume": 100},
            exits_note="MA 死叉离场；论点卖出条件优先于技术信号",
        ),
        "value_follow": Scheme(
            name="value_follow", title="价值跟随",
            desc="宇宙优先母项目筛选（回退配置池），重基本面轻择时，偏稳健",
            universe_source="screening", universe_limit=15,
            signal={"short_window": 5, "long_window": 20,
                    "buy_volume": 100, "sell_volume": 100},
            sizing={"max_orders_per_run": 2, "max_order_value": 15000.0},
            exits_note="论点卖出条件一票否决；无明确机会就持有不动",
        ),
        "defense": Scheme(
            name="defense", title="防御持有",
            desc="只卖不买：持有不动或逢死叉离场，大盘环境差时用",
            universe_source="config", allow_buy=False,
            signal={"short_window": 10, "long_window": 30,
                    "buy_volume": 100, "sell_volume": 100},
            exits_note="只卖不买；MA 死叉或论点恶化即离场",
        ),
    }


def _read_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def custom_schemes(root: str | Path = ".") -> dict[str, Scheme]:
    """用户自定义方案（strategy.local.yamlgitignored）。"""
    raw = _read_yaml(Path(root) / LOCAL_FILE)
    out: dict[str, Scheme] = {}
    for name, c in (raw.get("custom") or {}).items():
        if not isinstance(c, dict):
            continue
        u = c.get("universe") or {}
        out[str(name)] = Scheme(
            name=str(name), title=str(c.get("title", name)),
            desc=str(c.get("desc", "")), source="custom",
            universe_source=str(u.get("source", "config")),
            universe_tag=str(u.get("tag", "")),
            universe_limit=int(u.get("limit", 20)),
            signal=dict(c.get("signal") or {}),
            sizing=dict(c.get("sizing") or {}),
            risk=dict(c.get("risk") or {}),
            allow_buy=bool(c.get("allow_buy", True)),
            exits_note=str(c.get("exits_note", "")),
        )
    return out


def active_name(root: str | Path = ".", default: str = "ma_trend") -> tuple[str, str]:
    """当前选中方案名；返回 (name, 来源)。CLI > strategy.local.yaml > config 缺省。"""
    raw = _read_yaml(Path(root) / LOCAL_FILE)
    name = str(raw.get("active", "") or default)
    src = "local" if raw.get("active") else "default"
    return name, src


def mother_schemes() -> dict[str, Scheme]:
    """母策略转为可选方案（mother:growth 这类名），仅母项目可读时存在。

    母策略是选股侧语言：转为方案时宇宙=screening+tag，执行沿用通用规则。
    检测走环境变量/默认路径，与 repo root 无关。
    """
    out: dict[str, Scheme] = {}
    for m in mother_strategies():
        key = str(m.get("key", ""))
        if not key:
            continue
        out[f"mother:{key}"] = Scheme(
            name=f"mother:{key}", title=f"价值·{m.get('name', key)}",
            desc=str(m.get("desc", "")) or "母项目价值策略",
            source="mother", available=True,
            universe_source="screening", universe_tag=key, universe_limit=15,
            exits_note="遵循价值纪律：论点恶化或卖出条件触发即离场，不过度交易",
        )
    return out


def all_schemes(root: str | Path = ".") -> dict[str, Scheme]:
    d = builtin_schemes()
    d.update(custom_schemes(root))
    d.update(mother_schemes())
    return d


def resolve_scheme(name: str, root: str | Path = ".") -> Scheme:
    """按名取方案，未知名/母策略不可用时回退 ma_trend（fail-safe，不抛错）。"""
    schemes = all_schemes(root)
    hit = schemes.get(name)
    if hit and hit.available:
        return hit
    return schemes["ma_trend"]


def set_active(root: str | Path, name: str) -> tuple[bool, str]:
    """设置当前方案（只写 gitignored 的 strategy.local.yaml，保留 custom 块）。

    mother:* 系列即使当下检测不到母项目也允许保存（运行时回退 ma_trend 并注明），
    避免在开发机上无法预选生产环境方案。

    Returns:
        (ok, message)
    """
    from paper_trading.strategy.schemes import all_schemes as _all

    root_p = Path(root)
    if name not in _all(root_p):
        if not (name.startswith("mother:") and len(name) > len("mother:")):
            return False, f"未知方案：{name}"
    try:
        import yaml  # type: ignore
    except ImportError:
        return False, "缺少 pyyaml"
    p = root_p / LOCAL_FILE
    raw: dict = {}
    if p.exists():
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
    raw["active"] = name
    try:
        p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return True, f"已切换为 {name}"
    except Exception as e:
        return False, f"写入失败：{e}"


def mother_strategies(mother: str | Path | None = None) -> list[dict]:
    """母项目 strategies.yaml 只读展示（选股侧语言，供参照）。"""
    from paper_trading.integration.settings import mother_dir

    md = mother_dir(str(mother) if mother else None)
    if md is None:
        return []
    raw = _read_yaml(md / "config" / "strategies.yaml")
    out = []
    for key, v in raw.items():
        if not isinstance(v, dict):
            continue
        out.append({"key": key, "name": v.get("name", key),
                    "desc": v.get("desc", ""),
                    "n_rules": len([k for k in v if k not in ("name", "desc")])})
    return out
