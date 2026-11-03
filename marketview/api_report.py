"""일일 리포트 API. 생성은 동기(요청 하나가 몇 초~수십 초), 수집·화면과 독립이다."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db
from .config.settings import reports_dir
from .report import auto, run

router = APIRouter(prefix="/api/report")


def public(row: dict | None) -> dict | None:
    """화면에 내려줄 필드. 파일 경로는 서버가 /file 로만 열어준다."""
    if row is None:
        return None
    return {k: row[k] for k in ("id", "report_date", "version", "pdf_status", "drive_status", "message", "created_at", "origin")} | {
        "has_pdf": bool(row["pdf_path"]), "name": Path(row["md_path"]).stem}


@router.get("/last")
def last() -> dict:
    conn = db.connect()
    try:
        return {"run": public(run.last_run(conn)), "telegram": run.telegram_enabled(conn),
                "auto": auto.enabled(conn), "auto_time": auto.AUTO_TIME.strftime("%H:%M"), "auto_last": auto.last_result(conn)}
    finally:
        conn.close()


class SettingsIn(BaseModel):
    telegram: bool | None = None
    auto: bool | None = None


@router.put("/settings")
def put_settings(body: SettingsIn) -> dict:
    conn = db.connect()
    try:
        if body.telegram is not None:
            run.set_telegram_enabled(conn, body.telegram)
        if body.auto is not None:
            auto.set_enabled(conn, body.auto)
        return {"telegram": run.telegram_enabled(conn), "auto": auto.enabled(conn)}
    finally:
        conn.close()


@router.post("")
def create(date: str | None = None) -> dict:
    if not run.generate_lock.acquire(blocking=False):
        raise HTTPException(409, "리포트를 만드는 중입니다")
    conn = None
    try:
        conn = db.connect()
        return {"run": public(run.generate(conn, date))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if conn is not None:
            conn.close()
        run.generate_lock.release()


@router.get("/file")
def file(id: int, kind: str = "pdf") -> FileResponse:
    if kind not in ("pdf", "md"):
        raise HTTPException(400, "kind 는 pdf / md")
    conn = db.connect()
    try:
        row = conn.execute("SELECT md_path, pdf_path FROM report_run WHERE id=?", (id,)).fetchone()
    finally:
        conn.close()
    path = Path((row["pdf_path"] if kind == "pdf" else row["md_path"]) or "") if row else None
    if path is not None and not path.is_file() and path.name:
        # 폴더를 옮기면 DB 에 저장된 절대 경로가 깨진다(2026-10-11 바탕화면 → 사용자 폴더).
        # reports/YYYY/MM/<파일명> 구조는 그대로이므로 현재 reports 폴더 기준으로 다시 찾는다.
        moved = reports_dir() / path.name[:4] / path.name[5:7] / path.name
        path = moved if moved.is_file() else path
    if path is None or not path.is_file():
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    media = "application/pdf" if kind == "pdf" else "text/markdown; charset=utf-8"
    return FileResponse(path, media_type=media, headers={"Content-Disposition": f'inline; filename="{path.name}"'})
