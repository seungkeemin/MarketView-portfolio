"""DB 현황 요약 (읽기 전용): 계열별 행 수·구간·최신값, 최근 수집 실패."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marketview.config.settings import db_path  # noqa: E402

conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
total = conn.execute("SELECT COUNT(*), MIN(obs_date), MAX(obs_date), COUNT(DISTINCT series_key) FROM observation").fetchone()
print("관측치 합계 (행, 최초, 최신, 계열수):", total)
print(f"{'series':16}{'kind':9}{'rows':>6}  {'first':10}  {'last':10}  {'last_value':>12}")
for r in conn.execute("""SELECT o.series_key, o.kind, COUNT(*) n, MIN(o.obs_date) a, MAX(o.obs_date) b
                         FROM observation o GROUP BY 1,2 ORDER BY 1,2"""):
    last = conn.execute("SELECT value FROM observation WHERE series_key=? AND kind=? ORDER BY obs_date DESC, obs_ts DESC LIMIT 1",
                        (r[0], r[1])).fetchone()[0]
    print(f"{r[0]:16}{r[1]:9}{r[2]:6}  {r[3]:10}  {r[4]:10}  {last:12.4f}")
print("--- 최근 실행 중 ok 가 아닌 항목")
for r in conn.execute("""SELECT run_id, source, series_key, status, message FROM ingest_result
                         WHERE status!='ok' ORDER BY run_id DESC LIMIT 40"""):
    print(tuple(r))
