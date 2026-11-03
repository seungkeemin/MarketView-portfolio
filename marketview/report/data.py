"""리포트 내용 조립 (SPEC §9). DB 에서 읽어 블록 목록으로 만든다 — Markdown 과 HTML 이 같은 블록을 렌더링한다.

값이 없으면 "미수집" 이다. 0 이나 추정치로 채우지 않는다. 시세는 리포트 기준일까지의 '일별 종가'만 쓴다(장중 값 제외).
블록: ("h1"|"h2"|"h3"|"p"|"note", text) · ("table", headers, rows, aligns) · ("img", relpath, alt) · ("list", [items])
링크가 있는 셀/항목은 (text, url) 튜플.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from .. import events, ingest, news, queries, userdata
from ..config import layout
from ..config.settings import KST, today_kst

WEEKDAY = "월화수목금토일"
MISSING = "미수집"
MANUAL_MISSING = "소스 없음 · 수동 입력"
NEWS_PER_TAB = 6
TG_PER_CHANNEL = 4
RECENT_DAYS = 2            # 뉴스·텔레그램은 기준일 포함 최근 이 일수 안의 것만 싣는다
EVENT_DAYS = 14

Block = tuple


def _clean(text: str | None) -> str:
    return " ".join((text or "").replace("|", "/").split())


def fmt_level(state: dict, meta: dict) -> str:
    v, dec, unit = state["value"], meta["decimals"], meta["unit"]
    s = f"{v:,.{dec}f}"
    return s + {"pct": "%", "bp": "bp", "krw": "원"}.get(unit, "")


def fmt_change(state: dict, meta: dict) -> str:
    c = state["change"]
    if c is None:
        return "—"
    cu = state["change_unit"]
    if cu == "bp":
        return f"{c:+.1f}bp"
    if cu == "%":
        return f"{c:+.2f}%"
    return f"{c:+,.{meta['decimals']}f}"


def summary_blocks(conn: sqlite3.Connection, day: str) -> tuple[list[Block], list[str], list[dict]]:
    """자산군별 요약표. (블록, 수동 입력인데 값이 없는 항목 이름, 표에 쓰인 값의 출처 목록)."""
    blocks: list[Block] = []
    unfilled: list[str] = []
    used: list[dict] = []
    for tab, spec in layout.TABS.items():
        data = queries.tiles(conn, tab, day)
        blocks.append(("h3", spec["label"]))
        for g in data["groups"]:
            rows = []
            for item in g["tiles"]:
                st = item["state"]
                if st is None:
                    if item["manual"]:
                        unfilled.append(item["label"])     # 소스 없음 항목은 7절에 모아서 보여준다 (숨기지 않는다)
                        continue
                    rows.append([item["label"], MISSING, "—", "—", "—"])
                    continue
                used.append({"key": item["key"], "label": item["label"], "src": st["src"], "obs_date": st["obs_date"],
                             "provider": st["provider"]})
                rows.append([item["label"], fmt_level(st, item), fmt_change(st, item), st["obs_date"], st["src"]])
            if not rows:
                continue
            if g["label"]:
                blocks.append(("p", f"**{g['label']}**"))
            blocks.append(("table", ["지표", "레벨", "전일 대비", "관측일", "출처"], rows, ["l", "r", "r", "c", "l"]))
    return blocks, unfilled, used


def _data_basis(conn: sqlite3.Connection, day: str) -> str:
    kr = queries.tile_state(conn, "ktb_3y", day)
    us = queries.tile_state(conn, "ust_10y", day)
    return f"데이터 기준: 국내 {kr['obs_date'] if kr else MISSING} / 미국 {us['obs_date'] if us else MISSING}"


def news_blocks(conn: sqlite3.Connection, day: str) -> list[Block]:
    since = (date.fromisoformat(day) - timedelta(days=RECENT_DAYS - 1)).isoformat()
    out: list[Block] = []
    for tab, label in (("bond", "채권"), ("fx", "외환"), ("credit", "신용"), ("equity", "주식")):
        items = [i for i in news.feed(conn, tab, "", day, 60, "news") if i["published_at"][:10] >= since][:NEWS_PER_TAB]
        out.append(("h3", label))
        if not items:
            out.append(("p", "해당 기간 수집된 기사 없음"))
            continue
        out.append(("table", ["제목", "출처", "시각"],
                    [[(_clean(i["title"]), i["link"]), _clean(i["source"]), i["published_at"][5:16].replace("T", " ")] for i in items],
                    ["l", "l", "c"]))
    return out


def telegram_blocks(conn: sqlite3.Connection, day: str) -> list[Block]:
    since = (date.fromisoformat(day) - timedelta(days=RECENT_DAYS - 1)).isoformat()
    items = [i for i in news.feed(conn, None, "", day, 300, "telegram") if i["published_at"][:10] >= since]
    if not items:
        return [("p", "해당 기간 수집된 메시지 없음 (텔레그램 미연결이면 설정 > 텔레그램 확인)")]
    by_channel: dict[str, list[dict]] = {}
    for i in items:
        by_channel.setdefault(i["source"].removeprefix("텔레그램 · "), []).append(i)
    out: list[Block] = []
    for channel, rows in by_channel.items():
        out.append(("h3", channel))
        out.append(("list", [(f"{_clean(r['title'])[:100]} · {r['published_at'][5:16].replace('T', ' ')}", r["link"]) for r in rows[:TG_PER_CHANNEL]]))
    return out


def event_blocks(conn: sqlite3.Connection, day: str) -> list[Block]:
    evs = events.list_events(conn, date.fromisoformat(day), EVENT_DAYS)
    if not evs:
        return [("p", f"향후 {EVENT_DAYS}일 이벤트 없음")]
    rows = []
    for e in evs:
        when = f"{e['kst_date'][5:]} ({e['weekday']})" + (f" {e['kst_time']}" if e["kst_time"] else " 시각 미확인")
        rows.append([f"D-{e['d_day']}" if e["d_day"] else "D-day", when, e["region"], "★" * e["importance"],
                     _clean(e["title"]), _clean(e["consensus"]) or "—"])
    return [("table", ["D-day", "일시 (KST)", "지역", "중요도", "이벤트", "컨센서스"], rows, ["c", "l", "c", "l", "l", "l"])]


def position_blocks(conn: sqlite3.Connection, day: str) -> list[Block]:
    out: list[Block] = []
    pos = userdata.list_positions(conn, include_closed=False, asof=day)
    if not pos:
        out.append(("p", "열린 포지션 없음"))
        return out
    rows = []
    for p in pos:
        dec = p["decimals"]
        cur = f"{p['current']:,.{dec}f}" if p["current"] is not None else (MISSING if p["series_key"] else "연결 안 됨")
        pnl = f"{p['pnl']:+,.1f}{p['pnl_unit']}" if p["pnl"] is not None else "—"
        rows.append([_clean(p["product"]), "롱" if p["side"] == "long" else "숏", f"{p['entry_level']:,.{dec}f}", p["entry_date"], cur,
                     p["current_obs"] or "—", pnl, f"{p['target']:,.{dec}f}" if p["target"] is not None else "—",
                     f"{p['stop']:,.{dec}f}" if p["stop"] is not None else "—"])
    out.append(("table", ["상품", "방향", "진입", "진입일", "현재", "관측일", "손익", "목표", "손절"], rows,
                ["l", "c", "r", "c", "r", "c", "r", "r", "r"]))
    out.append(("note", "손익은 방향 기준 레벨 변화입니다 (금리·스프레드 bp, 환율 원, 지수 pt). 금리(%) 항목의 롱은 채권 롱(금리 하락 시 이익), "
                        "스프레드·가격·지수·환율의 롱은 레벨 상승 시 이익입니다. 현재 레벨은 기준일까지의 마지막 일별 종가입니다 (관측일 열 참고)."))
    return out


def failures(conn: sqlite3.Connection) -> list[dict]:
    """종류별 가장 최근 수집의 실패 항목 (api.status 와 같은 기준)."""
    return ingest.latest_failures(conn)


def source_blocks(conn: sqlite3.Connection, used: list[dict], unfilled: list[str], chart_notes: list[str]) -> list[Block]:
    out: list[Block] = []
    by_src: dict[str, list[dict]] = {}
    for u in used:
        by_src.setdefault(u["src"], []).append(u)
    rows = [[src, str(len(v)), max(x["obs_date"] for x in v), layout.PROVIDER_LAG.get(v[0]["provider"], "") or "—"]
            for src, v in sorted(by_src.items())]
    out.append(("table", ["출처", "표시한 지표 수", "가장 최근 관측일", "게시 지연"], rows, ["l", "r", "c", "l"]))
    fails = failures(conn)
    out.append(("h3", "수집 실패"))
    if fails:
        out.append(("list", [f"{f['source']} · {f['series_key']} — {_clean(f['message'])[:120]}" for f in fails]))
    else:
        out.append(("p", "가장 최근 수집에서 실패한 항목 없음"))
    out.append(("h3", "수동 입력 대기 (소스 없음)"))
    out.append(("p", ", ".join(unfilled) if unfilled else "없음"))
    if chart_notes:
        out.append(("h3", "차트 생성 메모"))
        out.append(("list", chart_notes))
    return out


def build(conn: sqlite3.Connection, day: str, charts: dict[str, str | None], chart_notes: list[str],
          telegram_on: bool, telegram_off_note: str = "설정에서 꺼져 있음") -> list[Block]:
    """charts: {이름: 상대경로 또는 None(데이터 없음/실패)}."""
    d = date.fromisoformat(day)
    created = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    summary, unfilled, used = summary_blocks(conn, day)
    blocks: list[Block] = [
        ("h1", f"MarketView 일일 리포트 — {day} ({WEEKDAY[d.weekday()]})"),
        ("p", f"생성: {created} KST · {_data_basis(conn, day)}"),
    ]
    if d < today_kst():
        blocks.append(("note", "기준일 이후에 만든 리포트입니다. 시세·포지션은 기준일까지의 관측만 사용했고, 뉴스·텔레그램은 "
                               "그 시점에 수집되어 있던 것만 담깁니다 (앱이 꺼져 있던 시간대의 기사는 빠질 수 있습니다)."))
    blocks += [
        ("h2", "1. 요약"), *summary,
        ("h2", "2. 차트"),
    ]
    for name, title in CHART_TITLES.items():
        path = charts.get(name)
        blocks.append(("img", path, title) if path else ("p", f"{title}: {MISSING}"))
    blocks += [("h2", "3. 뉴스"), *news_blocks(conn, day), ("h2", "4. 텔레그램 리서치")]
    blocks += telegram_blocks(conn, day) if telegram_on else [("p", telegram_off_note)]
    blocks += [("h2", f"5. 다가오는 이벤트 (향후 {EVENT_DAYS}일)"), *event_blocks(conn, day),
               ("h2", "6. 포지션"), *position_blocks(conn, day),
               ("h2", "7. 데이터 출처·관측일·수집 실패"), *source_blocks(conn, used, unfilled, chart_notes)]
    return blocks


CHART_TITLES = {
    "ktb_curve": "국고채 커브 (당일 / 1주 전 / 1개월 전)",
    "ust_curve": "UST 커브 (당일 / 1주 전 / 1개월 전)",
    "spreads": "주요 스프레드 (bp, 최근 3개월)",
    "credit": "신용 스프레드 (bp, 최근 3개월)",
    "fx": "외환 (최근 1개월, 시작일=100)",
    "equity": "주가지수 (최근 1개월, 시작일=100)",
}
