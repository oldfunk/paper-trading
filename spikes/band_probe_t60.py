"""对照实验（现网 scratch 库副本上跑，只读生产）：raw 缺不缺 T-1 那根，当日带从哪天算。"""
import datetime
import sqlite3

from paper_trading.data.db_manager import DataDBManager
from paper_trading.integration.rq_engine.store import RawMinuteBarStore

TODAY = datetime.date.today().isoformat()
DB = "data_today.db"
SYMS = ["002192"]


def probe(label):
    db = DataDBManager(DB)
    ro = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    last = ro.execute("SELECT MAX(substr(timestamp,1,10)) FROM daily_bars_raw"
                      " WHERE symbol='002192'").fetchone()[0]
    ro.close()
    st = RawMinuteBarStore.from_data_db(db, SYMS, today=TODAY)
    bars = st.get_bars("002192.XSHE")
    print(f"{label}: raw 最新={last} 拒供={st.gate_rejected}")
    if len(bars):
        print(f"    当日带 prev_close={float(bars['prev_close'][-1]):.2f} "
              f"limit_up={float(bars['limit_up'][-1]):.2f} "
              f"limit_down={float(bars['limit_down'][-1]):.2f}")


probe("A 现状（raw 已补到 T-1）")

c = sqlite3.connect(DB)
c.execute("DELETE FROM daily_bars_raw WHERE symbol='002192' AND substr(timestamp,1,10)='2026-11-02'")
c.commit()
c.close()
probe("B 把 T-1 那根删掉（=今早 15:40 采集后的真实状态）")
