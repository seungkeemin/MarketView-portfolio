"""백업 · 주간 CSV · 복원 (SPEC §10).

  python -m marketview.backup daily            # 오늘 백업 (이미 있으면 건너뜀, --force 면 다시)
  python -m marketview.backup csv              # 주간 CSV.gz (--force 면 7일 안이라도)
  python -m marketview.backup all              # 둘 다 필요한 만큼
  python -m marketview.backup restore 파일.db.gz [--to 경로] [--force]

로컬   backups/daily/marketview-YYYYMMDD.db.gz   (최근 30개)   backups/monthly/marketview-YYYYMM.db.gz (영구)
Drive  MarketView\\backups\\{daily,monthly,csv}\\  (같은 규칙)       exports/ 에는 주간 CSV.gz 가 누적된다.

규칙
- 원본 DB 는 읽기만 한다 (VACUUM INTO 는 일관된 복사본을 만든다. 서버가 켜져 있어도 안전).
- 롤링 삭제는 daily 폴더의 `marketview-YYYYMMDD.db.gz` 패턴 파일에만 적용한다. 그 밖의 파일은 건드리지 않는다.
- 월별 파일은 그 달의 마지막 일일 백업으로 계속 갱신되다가 달이 바뀌면 굳는다. 지난달 파일은 다시 쓰지 않는다.
- Drive 로 가는 DB·CSV 에서는 텔레그램 메시지를 뺀다 (CLAUDE.md 불변 규칙 6: 메시지는 클라우드에 올리지 않는다).
  로컬 백업에는 전부 들어 있다.
- Drive 복사 실패는 로컬 백업을 막지 않고 결과에 남긴다.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from . import db
from .collect.base import scrub
from .config.settings import backups_dir, drive_root, exports_dir, now_kst, today_kst

DAILY_KEEP = 30
DAILY_RE = re.compile(r"^marketview-(\d{8})\.db\.gz$")
CSV_RE = re.compile(r"^marketview-(\d{8})-observation\.csv\.gz$")
CSV_EVERY_DAYS = 7
TG_TABLES = ("tg_message_edit", "tg_message")     # tg_fts 는 트리거로 함께 비워진다
CSV_TABLES = {
    "observation": "SELECT series_key, obs_date, kind, obs_ts, value, provider, origin, captured_at, note "
                   "FROM observation ORDER BY series_key, obs_date, kind, obs_ts",
    "news": "SELECT id, published_at, source, tab, title, summary, link FROM news ORDER BY id",
}


def _gzip(src: Path, dst: Path) -> None:
    part = dst.with_name(dst.name + ".part")
    with open(src, "rb") as fi, gzip.open(part, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)
    part.replace(dst)


def _copy(src: Path, dst: Path) -> None:
    """.part 로 복사한 뒤 이름을 바꾼다. 중간에 끊겨도 대상 자리에 잘린 파일이 생기지 않는다(Drive 가 반쪽 .gz 를 올리거나
    월별 영구본이 깨지지 않게). 실패하면 .part 를 지우고 기존 대상 파일은 그대로 둔다."""
    part = dst.with_name(dst.name + ".part")
    try:
        shutil.copyfile(src, part)
        part.replace(dst)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _gunzip(src: Path, dst: Path) -> None:
    with gzip.open(src, "rb") as fi, open(dst, "wb") as fo:
        shutil.copyfileobj(fi, fo)


def _check(path: Path) -> None:
    """무결성 검사. 이상이 있으면 예외. 마이그레이션 표가 있는지도 본다."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        res = conn.execute("PRAGMA integrity_check").fetchall()
        if [r[0] for r in res] != ["ok"]:
            raise RuntimeError("무결성 검사 실패: " + "; ".join(str(r[0]) for r in res[:3]))
        conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    finally:
        conn.close()


def table_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE '%\\_fts%' ESCAPE '\\' ORDER BY name")]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


def _strip_telegram(src: Path, dst: Path) -> None:
    shutil.copyfile(src, dst)
    conn = sqlite3.connect(dst)
    try:
        for t in TG_TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.execute("INSERT INTO tg_fts(tg_fts) VALUES ('rebuild')")      # 빈 원본에서 다시 만들어 토큰 잔여를 없앤다
        conn.commit()
        conn.execute("VACUUM")                                              # 해제된 페이지의 옛 내용을 덮어쓴다
    finally:
        conn.close()


def _prune_daily(folder: Path) -> list[str]:
    """패턴에 맞는 파일만, 날짜 오름차순으로 오래된 것부터 DAILY_KEEP 개가 남도록 지운다."""
    if not folder.is_dir():
        return []
    files = sorted((f for f in folder.iterdir() if f.is_file() and DAILY_RE.match(f.name)), key=lambda f: f.name)
    gone = files[:-DAILY_KEEP] if len(files) > DAILY_KEEP else []
    for f in gone:
        f.unlink()
    return [f.name for f in gone]


def _drive_dir(*parts: str) -> Path:
    root = drive_root()
    if not root.exists():
        raise RuntimeError(f"Drive 폴더를 찾을 수 없습니다: {root}")
    return root.joinpath("backups", *parts)


def _record(conn: sqlite3.Connection, key: str, value: dict) -> None:
    conn.execute("INSERT INTO sync_state (key,value,updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                 (key, json.dumps(value, ensure_ascii=False), now_kst()))
    conn.commit()


def daily_exists(day: date) -> bool:
    return (backups_dir() / "daily" / f"marketview-{day:%Y%m%d}.db.gz").exists()


def backup_daily(conn: sqlite3.Connection, day: date | None = None, force: bool = False) -> dict:
    day = day or today_kst()
    stamp = f"{day:%Y%m%d}"
    name = f"marketview-{stamp}.db.gz"
    local = backups_dir() / "daily" / name
    result: dict = {"date": day.isoformat(), "file": name, "local": "skipped", "drive": "skipped", "pruned": [], "message": None}
    if local.exists() and not force:
        result["local"] = "exists"
        return result
    (backups_dir() / "daily").mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mv-backup-") as tmp:
        raw = Path(tmp) / "snap.db"
        conn.execute("VACUUM INTO ?", (str(raw),))
        _check(raw)
        _gzip(raw, local)
        result["local"] = "ok"
        result["pruned"] += _prune_daily(local.parent)
        monthly = backups_dir() / "monthly" / f"marketview-{day:%Y%m}.db.gz"
        monthly.parent.mkdir(parents=True, exist_ok=True)
        _copy(local, monthly)
        # Drive: 텔레그램 메시지를 뺀 사본
        try:
            cloud = Path(tmp) / "cloud.db"
            _strip_telegram(raw, cloud)
            _check(cloud)
            cloud_gz = Path(tmp) / name
            _gzip(cloud, cloud_gz)
            (d_daily, d_month) = _drive_dir("daily"), _drive_dir("monthly")
            d_daily.mkdir(parents=True, exist_ok=True)
            d_month.mkdir(parents=True, exist_ok=True)
            _copy(cloud_gz, d_daily / name)
            _copy(cloud_gz, d_month / f"marketview-{day:%Y%m}.db.gz")
            result["pruned"] += _prune_daily(d_daily)
            result["drive"] = "ok"
        except Exception as exc:  # noqa: BLE001 - Drive 실패는 로컬 백업을 막지 않는다
            result["drive"] = "failed"
            notes.append("Drive: " + scrub(str(exc)))
    result["message"] = " / ".join(notes) or None
    _record(conn, "backup_daily", result)
    return result


def csv_due(day: date | None = None) -> bool:
    day = day or today_kst()
    folder = exports_dir()
    last = None
    if folder.is_dir():
        for f in folder.iterdir():
            m = CSV_RE.match(f.name)
            if m:
                last = max(last or m.group(1), m.group(1))
    if last is None:
        return True
    return day - date(int(last[:4]), int(last[4:6]), int(last[6:])) >= timedelta(days=CSV_EVERY_DAYS)


def _write_csv_gz(conn: sqlite3.Connection, sql: str, path: Path) -> int:
    part = path.with_name(path.name + ".part")
    n = 0
    with gzip.open(part, "wt", encoding="utf-8-sig", newline="") as fh:       # BOM: 엑셀이 한글을 바로 읽는다
        cur = conn.execute(sql)
        w = csv.writer(fh)
        w.writerow([c[0] for c in cur.description])
        for row in cur:
            w.writerow(tuple(row))
            n += 1
    part.replace(path)
    return n


def export_csv(conn: sqlite3.Connection, day: date | None = None, force: bool = False) -> dict:
    """시세·뉴스를 CSV.gz 로 누적 보관. 텔레그램은 포함하지 않는다(로컬 DB 백업에만 있다)."""
    day = day or today_kst()
    result: dict = {"date": day.isoformat(), "files": [], "rows": {}, "local": "skipped", "drive": "skipped", "message": None}
    if not force and not csv_due(day):
        result["local"] = "not-due"
        return result
    exports_dir().mkdir(parents=True, exist_ok=True)
    for table, sql in CSV_TABLES.items():
        name = f"marketview-{day:%Y%m%d}-{table}.csv.gz"
        result["rows"][table] = _write_csv_gz(conn, sql, exports_dir() / name)
        result["files"].append(name)
    result["local"] = "ok"
    try:
        dest = _drive_dir("csv")
        dest.mkdir(parents=True, exist_ok=True)
        for name in result["files"]:
            _copy(exports_dir() / name, dest / name)
        result["drive"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["drive"] = "failed"
        result["message"] = "Drive: " + scrub(str(exc))
    _record(conn, "backup_csv", result)
    return result


def retry_csv_drive(conn: sqlite3.Connection, last: dict) -> dict:
    """Drive 복사만 실패했던 CSV 를 다시 내보내지 않고 이미 만든 로컬 파일만 복사한다."""
    result = {k: v for k, v in last.items() if k != "at"}
    try:
        dest = _drive_dir("csv")
        dest.mkdir(parents=True, exist_ok=True)
        for name in result.get("files", []):
            _copy(exports_dir() / name, dest / name)
        result.update(drive="ok", message=None)
    except Exception as exc:  # noqa: BLE001
        result.update(drive="failed", message="Drive: " + scrub(str(exc)))
    _record(conn, "backup_csv", result)
    return result


def run_due(conn: sqlite3.Connection) -> dict:
    """오늘 백업이 없으면 만들고, 주간 CSV 가 밀렸으면 만든다. 앱이 주기적으로 부른다.
    Drive 복사 실패의 재시도는 Drive 폴더가 다시 보일 때만 한다 — 마운트 안 된 동안 15분마다 VACUUM·CSV 전체 내보내기를
    되풀이하고 CSV 가 주간이 아니라 날마다 쌓이던 문제(2026-10-15 리뷰)."""
    out: dict = {}
    last = last_status(conn)
    today = today_kst()
    drive_back = drive_root().exists()
    # Drive 복사가 실패했던 날은 Drive 가 돌아오면 다시 시도한다 (텔레그램을 뺀 사본을 새로 만들어야 해서 백업 전체를 다시 만든다)
    retry_daily = drive_back and last.get("daily", {}).get("drive") == "failed" and last["daily"].get("date") == today.isoformat()
    if not daily_exists(today) or retry_daily:
        out["daily"] = backup_daily(conn, today, force=retry_daily)
    if csv_due():
        out["csv"] = export_csv(conn, today)
    elif drive_back and last.get("csv", {}).get("drive") == "failed":
        out["csv"] = retry_csv_drive(conn, last["csv"])
    return out


def last_status(conn: sqlite3.Connection) -> dict:
    out = {}
    for key in ("backup_daily", "backup_csv"):
        row = conn.execute("SELECT value, updated_at FROM sync_state WHERE key=?", (key,)).fetchone()
        if row:
            out[key.removeprefix("backup_")] = {**json.loads(row["value"]), "at": row["updated_at"]}
    return out


def restore(gz: Path, target: Path, force: bool = False) -> dict:
    """백업 .db.gz 를 target 에 복원한다. 기존 DB 는 지우지 않고 옆으로 옮긴다. 서버를 끈 뒤에 쓴다."""
    if not gz.is_file():
        raise FileNotFoundError(f"백업 파일이 없습니다: {gz}")
    target = target.resolve()
    moved: list[str] = []
    if target.exists() and not force:
        raise FileExistsError(f"{target} 이(가) 이미 있습니다. 덮어쓰려면 --force (기존 파일은 옆으로 옮겨 보존합니다)")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mv-restore-", dir=target.parent) as tmp:
        staged = Path(tmp) / "restored.db"
        _gunzip(gz, staged)
        _check(staged)                                  # 검사에 통과한 뒤에만 기존 파일을 건드린다
        counts = table_counts(staged)
        stamp = now_kst()[:19].replace(":", "").replace("-", "").replace("T", "-")
        for suffix in ("", "-wal", "-shm"):
            old = Path(str(target) + suffix)
            if old.exists():
                aside = old.with_name(f"{old.name}.before-restore-{stamp}")
                old.replace(aside)
                moved.append(aside.name)
        staged.replace(target)
    return {"restored": str(target), "moved_aside": moved, "tables": counts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="marketview.backup")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("daily", "csv", "all"):
        p = sub.add_parser(name)
        p.add_argument("--force", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("file", type=Path)
    r.add_argument("--to", type=Path, default=None, help="기본: 현재 설정의 DB 경로")
    r.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "restore":
        from .config.settings import db_path
        try:
            print(json.dumps(restore(args.file, args.to or db_path(), args.force), ensure_ascii=False, indent=2))
        except (FileNotFoundError, FileExistsError, RuntimeError) as exc:
            print(f"복원 실패: {exc}", file=sys.stderr)
            return 1
        return 0
    conn = db.connect()
    try:
        db.migrate(conn)
        out = {}
        if args.cmd in ("daily", "all"):
            out["daily"] = backup_daily(conn, force=args.force)
        if args.cmd in ("csv", "all"):
            out["csv"] = export_csv(conn, force=args.force)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
