"""클라우드 수집기 (GitHub Actions 러너에서 실행). 로컬과 같은 marketview/collect 코드를 쓴다.

  python -m marketview.cloud collect --mode daily|news --out data/cloud

러너에는 DB 가 없다. 빈 임시 SQLite 로 수집한 뒤, 이번 회차에서 받은 행만 날짜별 JSON 에 합쳐 쓴다:

  <out>/YYYY-MM-DD/pages.json       일별 값 (kind=daily: ECOS·FRED·KRX·smbs·Yahoo 일별)
  <out>/YYYY-MM-DD/news.json        뉴스 RSS
  <out>/status.json                 마지막 회차 상태 (로컬 상단 바의 "클라우드 동기화")

같은 키는 한 번만 들어가므로(관측치 = series_key+obs_date+kind+obs_ts, 뉴스 = link) 같은 회차를 두 번 돌려도
결과가 같다. 텔레그램은 여기서 다루지 않는다 (세션·메시지는 클라우드에 올리지 않는다).
키(ECOS·FRED·KRX)는 환경변수(GitHub Secrets)로만 받고, 상태 파일의 메시지는 scrub 으로 키를 가린다.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

from . import db, ingest, news
from .config import news as news_cfg
from .collect.base import scrub
from .config.settings import now_kst, today_kst

RECENT_DAYS = 10          # 일별 소스는 최근 10일만 (정정·지연 게시 흡수, 빈 DB 라 3년 백필 금지)
MODES = ("daily", "news")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _merge_rows(path: Path, rows: list[dict], key: tuple[str, ...], newer_wins: bool) -> int:
    """기존 파일과 합친다. 반환: 파일의 총 행 수. 같은 키는 한 행만 남는다."""
    merged = {tuple(r[k] for k in key): r for r in _read_json(path, [])}
    changed = not path.exists()
    for r in rows:
        k = tuple(r[c] for c in key)
        old = merged.get(k)
        if old is not None and (not newer_wins or _same_but_captured(old, r)):
            continue          # 값이 같으면 수집 시각만 다른 행으로 파일을 바꾸지 않는다(회차마다 무의미한 git diff 방지)
        merged[k] = r
        changed = True
    out = [merged[k] for k in sorted(merged)]
    if changed:
        _write_json(path, out)
    return len(out)


def _same_but_captured(a: dict, b: dict) -> bool:
    return all(a.get(c) == b.get(c) for c in (a.keys() | b.keys()) - {"captured_at"})


def export(conn: sqlite3.Connection, out: Path, day: date) -> dict:
    """임시 DB 의 이번 회차 행을 날짜 폴더의 JSON 에 합친다."""
    folder = out / day.isoformat()
    obs_cols = "series_key, obs_date, kind, obs_ts, value, provider, captured_at, note"
    pages = [dict(r) for r in conn.execute(f"SELECT {obs_cols} FROM observation WHERE kind='daily'")]
    arts = [dict(r) for r in conn.execute(
        "SELECT link, title, summary, source, feed_id, published_at, tab, title_key, fetched_at FROM news")]
    key = ("series_key", "obs_date", "kind", "obs_ts")
    counts = {"snapshots": 0, "pages": len(pages), "news": len(arts)}
    if pages:
        _merge_rows(folder / "pages.json", pages, key, newer_wins=True)        # 정정값은 새 값이 이긴다
    if arts:
        _merge_rows(folder / "news.json", arts, ("link",), newer_wins=False)
    return counts


def collect(mode: str, out: Path, today: date | None = None, log=print) -> dict:
    """한 회차를 돌리고 JSON 과 status.json 을 쓴다. 일부 소스가 실패해도 나머지는 계속된다."""
    if mode not in MODES:
        raise ValueError(f"mode 는 {MODES}")
    today = today or today_kst()
    with tempfile.TemporaryDirectory(prefix="mv-cloud-") as tmp:
        conn = db.connect(Path(tmp) / "cloud.db")
        db.migrate(conn)
        failures: list[dict] = []
        try:
            if mode == "daily":
                ingest.run_daily(conn, today, origin="cloud", log=log, recent_days=RECENT_DAYS)
            run = ingest.Run(conn, "news", "cloud")
            for r in news.collect_rss(conn, tuple(f for f in news_cfg.FEEDS if f.cloud), origin="cloud"):
                run.record("rss", r["feed"], r["status"], r["new"], r["message"])
            run.finish()
            failures = [{"source": r["source"], "key": r["series_key"], "message": scrub(r["message"] or "")[:200]}
                        for r in conn.execute("SELECT source, series_key, message FROM ingest_result WHERE status='error'")]
            stats = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM ingest_result GROUP BY status")}
            counts = export(conn, out, today)
        finally:
            conn.close()
    total = sum(stats.values())
    run_status = "failed" if total and stats.get("ok", 0) == 0 else ("partial" if failures else "ok")
    status = {"updated_at": now_kst(), "mode": mode, "status": run_status, "results": stats, "exported": counts,
              "failures": failures, "github_run": os.environ.get("GITHUB_RUN_ID") or None}
    _write_json(out / "status.json", status)
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="marketview.cloud")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--mode", choices=MODES, required=True)
    c.add_argument("--out", type=Path, required=True)
    pr = sub.add_parser("prune", help="오래된 날짜 폴더 삭제 (cloud_housekeeping)")
    pr.add_argument("--dir", type=Path, required=True, help="data/cloud 폴더")
    sq = sub.add_parser("squash", help="커밋이 많으면 이력을 한 커밋으로 합쳐 force-push (cloud_housekeeping)")
    sq.add_argument("--repo", type=Path, required=True, help="cloud-data 가 체크아웃된 작업 폴더")
    args = ap.parse_args(argv)
    if args.cmd in ("prune", "squash"):
        from . import cloud_housekeeping as hk
        if args.cmd == "prune":
            gone = hk.prune_old_days(args.dir, today_kst())
            print(f"prune: {len(gone)}개 폴더 삭제 (보존 {hk.KEEP_DAYS}일)" + (f" — {gone[0]} ~ {gone[-1]}" if gone else ""))
            return 0
        try:
            res = hk.squash_if_needed(args.repo)
        except RuntimeError as exc:                      # squash 실패가 이미 푸시한 수집 결과를 되돌리지는 않는다
            print(f"squash 실패(다음 회차에 다시 확인): {scrub(str(exc))}")
            return 1
        print(f"squash: 커밋 {res['squashed']}개 → 1개" if res else "squash: 필요 없음")
        return 0
    status = collect(args.mode, args.out)
    print(json.dumps({k: status[k] for k in ("mode", "status", "results", "exported")}, ensure_ascii=False))
    for f in status["failures"]:
        print(f"  실패 {f['source']}/{f['key']}: {f['message']}")
    return 2 if status["status"] == "failed" else 0     # 전부 실패했을 때만 Actions 를 실패로 표시


if __name__ == "__main__":
    sys.exit(main())
