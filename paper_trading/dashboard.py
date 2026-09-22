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

PAGE = """<!doctype html>
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
async function refresh(){
 try{
  const [s,ops,pos,ord,nav]=await Promise.all(["/api/status","/api/ops?limit=30","/api/positions","/api/orders?limit=30","/api/nav?limit=60"].map(get));
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
  rows(document.getElementById("ops"),[["时间"],["动作"],["参数"],["结果"],["资产",1]],
    (ops.data||[]).map(r=>{let p={},res={};try{p=JSON.parse(r.params||"{}")}catch(e){}try{res=JSON.parse(r.result||"{}")}catch(e){}
    const d=new Date(r.timestamp);
    const ps=Object.entries(p).map(([k,v])=>`${k}=${Array.isArray(v)?v.join(","):v}`).join(" ");
    const ok=!!r.ok;
    let detail=ok?("成交价 "+(res.filled_price??res.status??"")+(res.orders!=null?(" 订单"+res.orders):"")):(esc(res.error||res.status||"被拒"));
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><b>${esc(r.action)}</b></td><td class="mut">${esc(ps)}</td><td class="${ok?"ok":"bad"}">${detail}</td><td class="num">${r.total_value_after!=null?fmt(r.total_value_after):"-"}</td></tr>`}).join("")||`<tr><td colspan="5" class="mut">暂无操作记录 —— AI 执行 run/buy/sell 后会出现在这里</td></tr>`);
  rows(document.getElementById("pos"),[["代码"],["总仓",1],["可用",1],["成本",1],["现价",1],["市值",1],["盈亏",1]],
    (st.positions||[]).map(p=>{const pn=(p.current_price-p.avg_cost)*p.total_volume;
    return `<tr><td><b>${esc(p.symbol)}</b></td><td class="num">${p.total_volume}</td><td class="num">${p.available_volume}</td><td class="num">${fmt(p.avg_cost)}</td><td class="num">${fmt(p.current_price)}</td><td class="num">${fmt(p.market_value,0)}</td><td class="num ${cls(pn)}">${pn>=0?"+":""}${fmt(pn)}</td></tr>`}).join("")||`<tr><td colspan="7" class="mut">空仓</td></tr>`);
  rows(document.getElementById("orders"),[["时间"],["方向"],["代码"],["数量",1],["价格",1],["状态"],["费用",1]],
    (ord.data||[]).map(r=>{const d=new Date(r.created_at);const buy=r.direction==1;
    const fee=(Number(r.commission)+Number(r.stamp_duty)+Number(r.transfer_fee));
    const stt=r.status=="filled"?`<span class="ok">已成交</span>`:`<span class="bad">已拒绝</span>`;
    return `<tr><td class="mut">${d.toLocaleString("zh-CN")}</td><td><span class="tag ${buy?"buy":"sell"}">${buy?"买入":"卖出"}</span></td><td><b>${esc(r.symbol)}</b></td><td class="num">${r.volume}</td><td class="num">${fmt(r.filled_price||r.limit_price)}</td><td>${stt}</td><td class="num">${fmt(fee)}</td></tr>`}).join("")||`<tr><td colspan="7" class="mut">暂无订单</td></tr>`);
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
