"""리포트 생성 오케스트레이터.

  reports/YYYY/MM/YYYY-MM-DD[-vN].md|pdf  +  reports/YYYY/MM/assets/YYYY-MM-DD[-vN]_*.png

같은 날짜를 다시 만들면 덮어쓰지 않고 -v2, -v3 …. PDF·Drive 복사·개별 차트가 실패해도 나머지는 완료되고,
실패 사유는 report_run.message 에 남는다. 수집·화면은 이 모듈과 무관하게 계속 돈다.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
from datetime import date
from pathlib import Path

from ..config.settings import canonical_date, drive_root, now_kst, reports_dir, today_kst
from ..collect.base import scrub
from . import data, pdf, render

generate_lock = threading.Lock()      # 동시에 두 번 눌러도 한 번에 하나만


def telegram_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM app_setting WHERE key='report_telegram'").fetchone()
    return row is None or row["value"] != "0"          # 기본 on


def set_telegram_enabled(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute("INSERT INTO app_setting (key,value) VALUES ('report_telegram',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if on else "0",))
    conn.commit()


def _next_version(conn: sqlite3.Connection, day: str, folder: Path) -> int:
    row = conn.execute("SELECT MAX(version) FROM report_run WHERE report_date=?", (day,)).fetchone()
    v = (row[0] or 0) + 1
    while (folder / _stem(day, v)).with_suffix(".md").exists():       # DB 에 없어도 파일이 있으면 덮어쓰지 않는다
        v += 1
    return v


def _stem(day: str, version: int) -> str:
    return day if version == 1 else f"{day}-v{version}"


def _copy_to_drive(files: list[Path], assets: list[Path], rel: Path) -> None:
    root = drive_root()
    if not root.exists():
        raise RuntimeError(f"Drive 폴더를 찾을 수 없습니다: {root} (Google Drive 앱 로그인·마운트 확인)")
    dest = root / "reports" / rel
    (dest / "assets").mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, dest / f.name)
    for a in assets:
        shutil.copy2(a, dest / "assets" / a.name)


DRIVE_TG_NOTE = "텔레그램 리서치는 로컬 리포트에만 포함됩니다 (Drive 사본에서는 제외)."


def _drive_variant(conn: sqlite3.Connection, day: str, chart_files: dict, chart_notes: list[str], folder: Path, stem: str,
                   pdf_ok: bool, tmp: Path, notes: list[str]) -> list[Path]:
    """Drive 로 보낼 파일. 텔레그램을 포함해 만든 로컬 리포트와 달리 4절을 뺀 사본을 임시 폴더에 새로 만든다
    (규칙 6: 텔레그램 메시지는 클라우드에 올리지 않는다). 텔레그램을 껐으면 로컬 파일을 그대로 쓴다."""
    md_local, pdf_local = folder / f"{stem}.md", folder / f"{stem}.pdf"
    if not telegram_enabled(conn):
        return [md_local, *([pdf_local] if pdf_ok else [])]
    blocks = data.build(conn, day, chart_files, chart_notes, False, DRIVE_TG_NOTE)
    md = tmp / f"{stem}.md"
    md.write_text(render.to_markdown(blocks), encoding="utf-8")
    files = [md]
    if pdf_ok:
        try:
            html_path = tmp / f".{stem}.print.html"
            html_path.write_text(render.to_html(blocks, folder / "assets", f"MarketView {stem}"), encoding="utf-8")
            pdf.html_to_pdf(html_path, tmp / f"{stem}.pdf")
            files.append(tmp / f"{stem}.pdf")
        except Exception as exc:  # noqa: BLE001 - Drive 용 PDF 실패는 Markdown 복사를 막지 않는다
            notes.append("Drive PDF: " + scrub(str(exc)))
    return files


def generate(conn: sqlite3.Connection, day: str | None = None, origin: str = "manual") -> dict:
    """리포트 한 벌을 만든다. 결과: report_run 한 행 + 상태 요약. origin: manual | auto | catchup."""
    day = canonical_date(day) if day else today_kst().isoformat()
    if date.fromisoformat(day) > today_kst():
        raise ValueError("미래 날짜의 리포트는 만들 수 없습니다")
    rel = Path(day[:4]) / day[5:7]
    folder = reports_dir() / rel
    folder.mkdir(parents=True, exist_ok=True)
    version = _next_version(conn, day, folder)
    stem = _stem(day, version)
    md_path, pdf_path = folder / f"{stem}.md", folder / f"{stem}.pdf"
    notes: list[str] = []

    from . import charts                 # matplotlib 은 리포트를 만들 때만 읽는다 (앱 기동이 ~0.4초 빨라지고 메모리도 덜 쓴다)
    chart_files, chart_notes = charts.make_all(conn, day, folder / "assets", stem)
    blocks = data.build(conn, day, chart_files, chart_notes, telegram_enabled(conn))
    md_path.write_text(render.to_markdown(blocks), encoding="utf-8")
    rid = conn.execute("INSERT INTO report_run (report_date,version,md_path,created_at,origin) VALUES (?,?,?,?,?)",
                       (day, version, str(md_path), now_kst(), origin)).lastrowid
    conn.commit()

    pdf_status = "failed"
    try:
        html_path = folder / f".{stem}.print.html"
        html_path.write_text(render.to_html(blocks, folder / "assets", f"MarketView {stem}"), encoding="utf-8")
        try:
            pdf.html_to_pdf(html_path, pdf_path)
            pdf_status = "ok"
        finally:
            html_path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - PDF 실패는 Markdown 을 막지 않는다
        notes.append("PDF: " + scrub(str(exc)))

    assets = [folder / "assets" / n for n in chart_files.values() if n]
    drive_status = "ok"
    try:
        with tempfile.TemporaryDirectory(prefix="mv-drive-") as tmp:
            files = _drive_variant(conn, day, chart_files, chart_notes, folder, stem, pdf_status == "ok", Path(tmp), notes)
            _copy_to_drive(files, assets, rel)
    except Exception as exc:  # noqa: BLE001 - Drive 실패는 로컬 결과를 막지 않는다
        drive_status = "failed"
        notes.append("Drive: " + scrub(str(exc)))

    notes += [f"차트 {n}" for n in chart_notes]
    conn.execute("UPDATE report_run SET pdf_path=?, pdf_status=?, drive_status=?, message=? WHERE id=?",
                 (str(pdf_path) if pdf_status == "ok" else None, pdf_status, drive_status, " / ".join(notes) or None, rid))
    conn.commit()
    return get_run(conn, rid)


def get_run(conn: sqlite3.Connection, rid: int) -> dict:
    return dict(conn.execute("SELECT * FROM report_run WHERE id=?", (rid,)).fetchone())


def last_run(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM report_run ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
