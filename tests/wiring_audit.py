"""R2.1/R2.2：AgentConfig 字段接线审计（反射）。

检查三件套：字段存在于 AgentConfig ⇄ cli.py 读取 agent_kwargs ⇄ config.yaml 有键。
用法：venv/bin/python -m tests.wiring_audit [--write-test]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def audit() -> dict:
    from paper_trading.agent.loop import AgentConfig

    fields = {k for k, v in AgentConfig.__dataclass_fields__.items()}
    cli_src = (ROOT / "paper_trading" / "cli.py").read_text(encoding="utf-8")
    m = re.search(r"self\.agent_cfg = AgentConfig\((.*?)\n        \)", cli_src, re.S)
    wired = set(re.findall(r"(\w+)=", m.group(1))) if m else set()
    cfg_src = (ROOT / "config.yaml").read_text(encoding="utf-8")
    am = re.search(r"^agent:\n((?:  .*\n|    .*\n)*)", cfg_src, re.M)
    ykeys = set(re.findall(r"^  (\w+):", am.group(1), re.M)) if am else set()
    # 有意不接线的（仅代码构造/测试用）：允许显式白名单
    WIRING_EXEMPT = set()
    return {
        "fields": fields,
        "wired": wired,
        "yaml": ykeys,
        "missing_in_cli": sorted(fields - wired - WIRING_EXEMPT),
        "missing_in_yaml": sorted((fields & wired) - ykeys - {"dry_run"}),
        "orphan_cli": sorted(wired - fields),
    }


def main() -> int:
    r = audit()
    print("AgentConfig fields:", len(r["fields"]))
    print("cli wired        :", len(r["wired"]))
    print("config.yaml keys :", len(r["yaml"]))
    print()
    print("!! 字段存在但 cli 未接线:", r["missing_in_cli"] or "无")
    print("!! 已接线但 yaml 无键   :", r["missing_in_yaml"] or "无")
    print("!! cli 传了不存在的字段  :", r["orphan_cli"] or "无")
    bad = bool(r["missing_in_cli"] or r["orphan_cli"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
