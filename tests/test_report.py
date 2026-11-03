"""일일 리포트: 미수집 표기, 같은 날 재생성 버전, PDF·Drive 실패 격리, 렌더링."""

from datetime import date

import pytest

from marketview import db
from marketview.collect.base import Fetched
from marketview.report import data, pdf, render, run

DAY = "2026-10-08"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETVIEW_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("MARKETVIEW_DRIVE_ROOT", str(tmp_path / "drive"))
    (tmp_path / "drive").mkdir()

    def fake_pdf(html_path, pdf_path):
        pdf_path.write_bytes(b"%PDF-1.4 " + b"x" * 2000)
    monkeypatch.setattr(pdf, "html_to_pdf", fake_pdf)
    return tmp_path


def _put(conn, key, d, value, provider="ecos"):
    y, m, dd = map(int, d.split("-"))
    db.upsert_observation(conn, Fetched(key, value, date(y, m, dd), provider), "daily", "local")
    conn.commit()


def test_missing_values_are_not_filled(conn, env):
    _put(conn, "ktb_3y", DAY, 3.98)          # ktb_10y 는 없다 → 국고 10−3 도 없어야 한다
    md = render.to_markdown(data.build(conn, DAY, {}, [], True))
    assert "| 국고 3년 | 3.980% |" in md
    assert "| 국고 10년 | 미수집 | — | — | — |" in md
    assert "| 국고 10−3 | 미수집 | — | — | — |" in md   # 파생값은 구성 값이 하나라도 없으면 None → 미수집 (0 아님)
    assert "0.000" not in md and "+0.0bp" not in md


def test_manual_without_source_listed_not_hidden(conn, env):
    md = render.to_markdown(data.build(conn, DAY, {}, [], True))
    section7 = md.split("## 7.")[1]
    assert "수동 입력 대기" in section7 and "한국 CDS 5Y" in section7


def test_change_uses_previous_observation(conn, env):
    _put(conn, "ktb_3y", "2026-10-07", 3.95)
    _put(conn, "ktb_3y", DAY, 3.98)
    md = render.to_markdown(data.build(conn, DAY, {}, [], True))
    assert "+3.0bp" in md


def test_asof_excludes_later_observations(conn, env):
    _put(conn, "ktb_3y", DAY, 3.98)
    _put(conn, "ktb_3y", "2026-10-09", 4.50)
    md = render.to_markdown(data.build(conn, DAY, {}, [], True))
    assert "4.500%" not in md and "3.980%" in md


def test_position_note_says_asof_close_not_latest(conn, env):
    """지난 날짜 리포트의 포지션 현재값은 기준일까지의 종가다 — 각주가 '최신 값' 이라고 하지 않는다."""
    _put(conn, "ktb_3y", DAY, 3.98)
    _put(conn, "ktb_3y", "2026-10-09", 4.50)
    conn.execute("INSERT INTO position (product,series_key,side,entry_level,entry_date,created_at,updated_at) "
                 "VALUES ('국고 3년','ktb_3y','short',4.10,'2026-10-01','x','x')")
    conn.commit()
    md = render.to_markdown(data.build(conn, DAY, {}, [], True)).split("## 6.")[1].split("## 7.")[0]
    assert "| 3.980 | 2026-10-08 |" in md and "4.500" not in md
    assert "기준일까지의 마지막 일별 종가" in md and "수집된 최신 값" not in md


def test_generate_versions_never_overwrite(conn, env):
    first = run.generate(conn, DAY)
    md1 = (env / "reports/2026/10/2026-10-08.md")
    assert md1.exists()
    body = md1.read_text(encoding="utf-8")
    second = run.generate(conn, DAY)
    assert (first["version"], second["version"]) == (1, 2)
    assert (env / "reports/2026/10/2026-10-08-v2.md").exists() and (env / "reports/2026/10/2026-10-08-v2.pdf").exists()
    assert md1.read_text(encoding="utf-8") == body                      # v1 은 그대로
    assert conn.execute("SELECT COUNT(*) FROM report_run").fetchone()[0] == 2
    assert (env / "drive/reports/2026/10/2026-10-08-v2.md").exists()    # Drive 에도 복사


def test_version_skips_existing_file_even_without_db_row(conn, env):
    folder = env / "reports/2026/10"
    folder.mkdir(parents=True)
    (folder / "2026-10-08.md").write_text("user file", encoding="utf-8")
    r = run.generate(conn, DAY)
    assert r["version"] == 2 and (folder / "2026-10-08.md").read_text(encoding="utf-8") == "user file"


def test_drive_failure_keeps_local_report(conn, env, monkeypatch):
    monkeypatch.setenv("MARKETVIEW_DRIVE_ROOT", str(env / "no-such-drive"))
    r = run.generate(conn, DAY)
    assert r["drive_status"] == "failed" and r["pdf_status"] == "ok"
    assert (env / "reports/2026/10/2026-10-08.pdf").exists() and "Drive" in r["message"]
    assert not (env / "no-such-drive").exists()                         # 없는 Drive 폴더를 만들어내지 않는다


def test_pdf_failure_keeps_markdown_and_drive_copy(conn, env, monkeypatch):
    def boom(html_path, pdf_path):
        raise RuntimeError("Chrome 없음")
    monkeypatch.setattr(pdf, "html_to_pdf", boom)
    r = run.generate(conn, DAY)
    assert r["pdf_status"] == "failed" and r["pdf_path"] is None and r["drive_status"] == "ok"
    assert (env / "reports/2026/10/2026-10-08.md").exists()
    assert not list((env / "reports/2026/10").glob(".*.html"))          # 임시 HTML 은 남지 않는다


def test_future_date_rejected(conn, env):
    with pytest.raises(ValueError):
        run.generate(conn, "2999-01-01")
    with pytest.raises(ValueError):
        run.generate(conn, "2026/10/08")


def test_telegram_toggle(conn, env):
    assert run.telegram_enabled(conn) is True
    run.set_telegram_enabled(conn, False)
    assert run.telegram_enabled(conn) is False
    md = render.to_markdown(data.build(conn, DAY, {}, [], run.telegram_enabled(conn)))
    assert "설정에서 꺼져 있음" in md


def test_table_cells_escape_pipes(conn, env):
    assert data._clean("a | b\nc") == "a / b c"
    md = render.to_markdown([("table", ["제목"], [[("x | y".replace("|", "/"), "https://e.com/a")]], ["l"])])
    assert "[x / y](https://e.com/a)" in md


def test_html_escapes_content():
    from pathlib import Path
    html = render.to_html([("p", "<script>alert(1)</script> **굵게**"), ("quote", "<b>")], Path("."), "t")
    assert "<script>alert" not in html and "<strong>굵게</strong>" in html


def test_drive_copy_excludes_telegram_but_local_keeps_it(conn, env):
    conn.execute("INSERT INTO tg_message (channel,channel_title,msg_id,body,posted_at,fetched_at) "
                 "VALUES ('@x','리서치X',1,'비밀리서치제목','2026-10-08T09:00:00+09:00','2026-10-08T09:00:00+09:00')")
    conn.commit()
    r = run.generate(conn, DAY)
    local = (env / "reports/2026/10/2026-10-08.md").read_text(encoding="utf-8")
    drive = (env / "drive/reports/2026/10/2026-10-08.md").read_text(encoding="utf-8")
    assert "비밀리서치제목" in local and "비밀리서치제목" not in drive
    assert "Drive 사본에서는 제외" in drive and r["drive_status"] == "ok"
    assert (env / "drive/reports/2026/10/2026-10-08.pdf").exists()


def test_drive_copy_is_identical_when_telegram_off(conn, env):
    run.set_telegram_enabled(conn, False)
    run.generate(conn, DAY)
    assert (env / "reports/2026/10/2026-10-08.md").read_bytes() == (env / "drive/reports/2026/10/2026-10-08.md").read_bytes()


def test_markdown_link_escapes_brackets_and_parens():
    md = render.to_markdown([("list", [("제목 [단독] 기사", "https://e.com/a_(1) b")])])
    assert md.strip() == r"- [제목 \[단독\] 기사](https://e.com/a_%281%29%20b)"


def test_rebase_uses_common_start_date():
    """시작일이 다른 시리즈를 '시작일=100' 으로 맞출 때는 모두 값이 있는 가장 이른 날부터 센다."""
    from marketview.report import charts
    d = lambda n: date(2026, 10, n)
    windows = [(0, "usdkrw", [d(1), d(2), d(5), d(6)], [100.0, 110.0, 120.0, 130.0], "ECOS"),
               (1, "dxy", [d(5), d(6)], [50.0, 55.0], "Yahoo")]
    out = charts.align_and_rebase(windows)
    assert out[0][2] == [d(5), d(6)] and out[0][3] == pytest.approx([100.0, 130.0 / 120.0 * 100])
    assert out[1][3] == pytest.approx([100.0, 110.0])
    assert charts.align_and_rebase([]) == []
    assert charts.align_and_rebase([(0, "a", [d(1), d(2)], [1.0, 2.0], "x"), (1, "b", [d(2)], [1.0], "y")]) == []     # 남은 점이 2개 미만이면 뺀다


def test_report_lock_is_released_when_db_connect_fails(env, monkeypatch):
    from fastapi.testclient import TestClient
    from marketview import api_report
    from marketview.app import app
    monkeypatch.setattr(api_report.db, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")))
    assert TestClient(app, raise_server_exceptions=False).post("/api/report").status_code == 500
    assert run.generate_lock.acquire(blocking=False)                       # 락이 새지 않았다
    run.generate_lock.release()
