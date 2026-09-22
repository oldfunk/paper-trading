"""只读 Web 仪表盘：直观展示账户、NAV、AI 操作流水。

用法:
    python -m paper_trading.dashboard --port 8080
    # 浏览器/手机打开 http://<pi-ip>:8080

纯标准库实现，不写任何数据（只读 SQLite），可与交易进程并存。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper Trading 仪表盘</title>
<style>
:root{--up:#e53935;--down:#1e8e3e;--bg:#0e1621;--panel:#17212b;--fg:#e7ecf1;--mut:#8a9bab;--line:#263340}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
header{padding:18px 22px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
h1{font-size:18px;margin:0;font-weight:600}
#clock{color:var(--mut);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;padding:16px 22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{color:var(--mut);font-size:12px;letter-spacing:.05em}
.card .v{font-size:26px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.card .s{font-size:12px;color:var(--mut);margin-top:2px}
section{padding:6px 22px 18px}
h2{font-size:14px;color:var(--mut);font-weight:600;margin:18px 0 8px;text-transform:uppercase;letter-spacing:.08em}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:9px 12px;text-align:left;font-size:13px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mut);font-weight:600;background:#1b2836}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)}
.ok{color:var(--down);font-weight:600} .bad{color:var(--up);font-weight:600}
.tag{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px}
.tag.buy{background:#3a1518;color:#ff8a80} .tag.sell{background:#0e2a17;color:#69f0ae}
.scroll{overflow-x:auto;border-radius:10px}
.mut{color:var(--mut)}
#navchart{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
</style>
</head>
<body>
<header><h1>📈 Paper Trading 仪表盘</h1><span id="clock">加载中…</span></header>

<div class="cards" id="cards"></div>

<section>
  <h2>净值走势 (NAV)</h2>
  <div class="scroll"><table id="nav"></table></div>
</section>

<section>
  <h2>AI 操作流水</h2>
  <div class="scroll"><table id="ops"></table></div>
</section>

<section>
  <h2>当前持仓</h2>
  <div class="scroll"><table id="pos"></table></div>
</section>

<section>
  <h2>订单记录</h2>
  <div class="scroll"><table id="orders"></table></div>
</section>

<script>
const fmt=(n,d=2)=>n==null?"-":Number(n).toLocaleString("zh-CN",{minimumFractionDigits:d,maximumFractionDigits:d});
const pct=n=>n==null?"-":(n*100).toFixed(2)+"%";
const cls=n=>n>0?"up":n<0?"down":"mut";
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function get(u){const r=await fetch(u);return r.json()}
function rows(t,head,body){t.innerHTML="<thead><tr>"+head.map(h=>`<th${h[1]?' class="num"':''}>${h[0]}</th>`).join("")+"</tr></thead><tbody>"+body+"</tbody>"}

/* ---- 中文映射（名称表来自 /api/names，均为行情源真实名称） ---- */
let NAMES={};                                   // {symbol: 真实名称}
const sym=c=>NAMES[c]?`${NAMES[c]} ${c}`:c;     // "贵州茅台 600519"
const ACTION_CN={
  "run":"每日结算","cron-run":"每日结算(cron)","run:dry-run":"试运行",
  "buy":"买入","sell":"卖出","preview":"试算","status":"查询","nav":"查询净值","history":"查询历史","names":"刷新名称"};
const ACTION_ICON={"run":"🚀","cron-run":"⏰","run:dry-run":"🧪","buy":"🔴 买入","sell":"🟢 卖出","preview":"🔍","status":"🔎","nav":"📈","history":"🗄","names":"🏷"};
function errCN(e){
  if(!e)return"";
  const M=[[/multiple of 100/i,"数量必须为100股整数倍"],[/Insufficient cash|need \d+, have/i,"资金不足"],
    [/Insufficient available/i,"可用持仓不足(T+1未解冻)"],[/Risk rejected/i,"风控拒绝"],
    [/exceeds max/i,"超单笔上限"],[/Position limit exceeded/i,"超单票仓位上限"],
    [/Total position limit/i,"超总仓位上限"],[/Drawdown halt/i,"回撤熔断"],
    [/over limit-up|above bar high/i,"超过当日可买价"],[/below limit-down|below bar low/i,"低于当日可卖价"],
    [/No data for/i,"无行情数据"],[/no limit price/i,"未指定价格"]];
  for(const[r,c]of M){if(r.test(e))return c}
  return e;
}
function paramCN(a,j){let p={};try{p=JSON.parse(j||"{}")}catch(e){}
  if(a.startsWith("run"))return`标的: ${(p.symbols||[]).map(sym).join("、")}`;
  if(a==="buy"||a==="sell")return `${sym(p.symbol)} ×${p.volume}股${p.price?" @限价"+fmt(p.price):""}`;
  if(a==="preview")return `${sym(p.symbol)} ${p.direction==="buy"?"买入":"卖出"} ×${p.volume}`;
  return Object.entries(p).map(([k,v])=>`${k}=${Array.isArray(v)?v.map(sym).join(","):sym(v)}`).join(" ");}
function resultCN(a,ok,j){let r={};try{r=JSON.parse(j||"{}")}catch(e){}
  if(!ok)return errCN(r.error||"被拒");
  if(a==="buy"||a==="sell")return`成交 @${fmt(r.filled_price)}，佣金${fmt(r.commission)}`;
  if(a.startsWith("run"))return`信号${r.signals??0}个 · 下单${r.orders??0}笔`;
  if(a==="preview")return r.ok?"通过":"未通过";
  return "完成";}
async function refresh(){
 try{
  const [s,ops,pos,ord,nav,nm]=await Promise.all(["/api/status","/api/ops?limit=30","/api/positions","/api/orders?limit=30","/api/nav?limit=60","/api/names"].map(get));
  NAMES=(nm&&nm.data)||{};
  document.getElementById("clock").textContent="更新于 "+new Date().toLocaleTimeString("zh-CN");
  const st=s.data;
  document.getElementById("cards").innerHTML=`
   <div class="card"><div class="k">总资产</div><div class="v">${fmt(st.total_value)}</div><div class="s">现金 + 持仓市值</div></div>
   <div class="card"><div class="k">浮动盈亏</div><div class="v ${cls(st.pnl)}">${st.pnl>=0?"+":""}${fmt(st.pnl)}</div><div class="s ${cls(st.pnl)}">${pct(st.pnl_pct)}</div></div>
   <div class="card"><div class="k">可用资金</div><div class="v">${fmt(st.cash)}</div></div>
   <div class="card"><div class="k">持仓市值</div><div class="v">${fmt(st.market_value)}</div><div class="s">${st.positions.length} 只标的</div></div>`;
  rows(document.getElementById("nav"),[["时间"],["总资产",1],["现金",1],["盈亏",1]],
    (nav.data||[]).reverse().map(r=>{const d=new Date(r.timestamp);const pn=Number(r.pnl);
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td class="num">${fmt(r.total_value)}</td><td class="num">${fmt(r.cash)}</td><td class="num ${cls(pn)}">${pn>=0?"+":""}${fmt(pn)}</td></tr>`}).join(""));
  rows(document.getElementById("ops"),[["时间"],["动作"],["参数"],["结果"],["资产后",1]],
    (ops.data||[]).map(r=>{const ok=!!r.ok;const d=new Date(r.timestamp);
    const label=ACTION_ICON[r.action]||ACTION_CN[r.action]||r.action;
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><b>${esc(label)}</b></td>`+
      `<td class="mut">${esc(paramCN(r.action,r.params))}</td>`+
      `<td class="${ok?"ok":"bad"}">${esc(resultCN(r.action,ok,r.result))}</td>`+
      `<td class="num">${r.total_value_after!=null?fmt(r.total_value_after):"-"}</td></tr>`}).join("")||`<tr><td colspan="5" class="mut">暂无操作记录 —— AI 执行结算/买入/卖出后会出现在这里</td></tr>`);
  rows(document.getElementById("pos"),[["标的"],["总仓",1],["可用",1],["成本",1],["现价",1],["市值",1],["盈亏",1]],
    (st.positions||[]).map(p=>{const pn=(p.current_price-p.avg_cost)*p.total_volume;
    return `<tr><td><b>${esc(sym(p.symbol))}</b></td><td class="num">${p.total_volume}</td><td class="num">${p.available_volume}</td><td class="num">${fmt(p.avg_cost)}</td><td class="num">${fmt(p.current_price)}</td><td class="num">${fmt(p.market_value,0)}</td><td class="num ${cls(pn)}">${pn>=0?"+":""}${fmt(pn)}</td></tr>`}).join("")||`<tr><td colspan="7" class="mut">空仓</td></tr>`);
  rows(document.getElementById("orders"),[["时间"],["方向"],["标的"],["数量",1],["价格",1],["状态"],["费用",1]],
    (ord.data||[]).map(r=>{const d=new Date(r.created_at);const buy=r.direction==1;
    const fee=(Number(r.commission)+Number(r.stamp_duty)+Number(r.transfer_fee));
    const stt=r.status=="filled"?`<span class="ok">✓ 已成交</span>`:`<span class="bad">✗ 已拒绝</span>`;
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><span class="tag ${buy?"buy":"sell"}">${buy?"买入":"卖出"}</span></td><td><b>${esc(sym(r.symbol))}</b></td><td class="num">${r.volume}</td><td class="num">${fmt(r.filled_price||r.limit_price)}</td><td>${stt}</td><td class="num">${fmt(fee)}</td></tr>`}).join("")||`<tr><td colspan="7" class="mut">暂无订单</td></tr>`);
 }catch(e){document.getElementById("clock").textContent="刷新失败: "+e}
}
refresh();setInterval(refresh,15000);
</script>
</body>
</html>"""


def _ro(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


class Handler(BaseHTTPRequestHandler):
    data_db = "data.db"
    account_db = "paper_account.db"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self._send(code, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif u.path == "/api/status":
                from paper_trading.hermes_bridge import HermesBridge

                b = HermesBridge(data_db=self.data_db, account_db=self.account_db)
                self._json({"ok": True, "data": b.get_status()})
            elif u.path == "/api/positions":
                from paper_trading.hermes_bridge import HermesBridge

                b = HermesBridge(data_db=self.data_db, account_db=self.account_db)
                self._json({"ok": True, "data": b.get_status()["positions"]})
            elif u.path == "/api/names":
                conn = _ro(self.data_db)
                try:
                    rows = conn.execute(
                        "SELECT symbol, name FROM stock_pool "
                        "WHERE name IS NOT NULL AND name != ''"
                    ).fetchall()
                    self._json({"ok": True, "data": {r["symbol"]: r["name"] for r in rows}})
                finally:
                    conn.close()
            elif u.path in ("/api/nav", "/api/ops", "/api/orders", "/api/fills"):
                q = parse_qs(u.query)
                limit = int(q.get("limit", ["30"])[0])
                conn = _ro(self.account_db)
                try:
                    table = {"nav": "nav_history", "ops": "op_log",
                             "orders": "orders", "fills": "fills"}[u.path.split("/")[2]]
                    order_col = {"nav_history": "id", "op_log": "id",
                                 "orders": "order_id", "fills": "fill_id"}[table]
                    rows = conn.execute(
                        f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                    self._json({"ok": True, "data": [dict(r) for r in rows]})
                finally:
                    conn.close()
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, code=500)

    def log_message(self, fmt: str, *args) -> None:  # quiet access log
        pass


def main() -> None:
    global_handler = Handler
    ap = argparse.ArgumentParser(description="Read-only paper trading dashboard")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data-db", default="data.db")
    ap.add_argument("--account-db", default="paper_account.db")
    args = ap.parse_args()
    global_handler.data_db = args.data_db
    global_handler.account_db = args.account_db

    srv = ThreadingHTTPServer((args.host, args.port), global_handler)
    print(f"Dashboard: http://{args.host}:{args.port}  ({datetime.now().isoformat()})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()


if __name__ == "__main__":
    main()
