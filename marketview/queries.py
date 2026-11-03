"""화면용 조회. DB 를 읽어 타일·시계열·커브를 계산한다 (파생값은 여기서 계산하고 저장하지 않는다).

값이 없으면 항목을 비우거나 None 으로 돌려준다. 0 이나 추정치로 채우지 않는다.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

from . import derived
from .config import layout
from .config.series import BY_KEY
from .config.settings import canonical_date, today_kst


def _scale(key: str) -> float:
    return layout.SCALE_TO_BP.get(key, 1.0)


def label_of(key: str) -> str:
    if key in derived.REGISTRY:
        return derived.REGISTRY[key].label
    return BY_KEY[key].label if key in BY_KEY else key


def meta_of(key: str) -> dict:
    """키 하나의 표시 메타 (단위·소수자리·변화 표기)."""
    if key in derived.REGISTRY:
        d = derived.REGISTRY[key]
        return {"key": key, "label": d.label, "unit": d.unit, "decimals": 1 if d.unit == "bp" else 3,
                "change_unit": "bp", "manual": False, "derived": True}
    s = BY_KEY[key]
    unit, decimals = s.unit, s.decimals
    if key in layout.SCALE_TO_BP:
        unit, decimals = "bp", 0
    change_unit = "bp" if (s.delta_unit == "bp" or unit == "bp") else ("%" if s.delta_unit == "pct" else "")
    return {"key": key, "label": s.label, "unit": unit, "decimals": decimals, "change_unit": change_unit,
            "manual": s.provider == "manual", "derived": False, "raw_delta": s.delta_unit}


def _daily(conn: sqlite3.Connection, key: str, asof: str | None, limit: int | None = None) -> list[tuple[str, float, str]]:
    """[(obs_date, value, provider)] 오름차순. limit 이 있으면 최근 limit 개."""
    sql = "SELECT obs_date, value, provider FROM observation WHERE series_key=? AND kind='daily'"
    args: list = [key]
    if asof:
        sql += " AND obs_date<=?"
        args.append(asof)
    if limit:
        sql += " ORDER BY obs_date DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()[::-1]
    else:
        rows = conn.execute(sql + " ORDER BY obs_date", args).fetchall()
    return [(r["obs_date"], r["value"], r["provider"]) for r in rows]


def _delta(meta: dict, value: float, prev: float | None) -> tuple[float | None, str]:
    if prev is None:
        return None, meta["change_unit"]
    if meta["change_unit"] == "bp":
        # 원본이 % 인 금리는 ×100, 이미 bp 인 값(스프레드·OAS 표시값)은 그대로 뺀다
        return (value - prev) * (100 if meta["unit"] == "pct" else 1), "bp"
    if meta["change_unit"] == "%":
        return ((value / prev - 1) * 100 if prev else None), "%"
    return value - prev, ""


def tile_state(conn: sqlite3.Connection, key: str, asof: str | None) -> dict | None:
    meta = meta_of(key)
    if meta["derived"]:
        pts = derived.recent(conn, key, end=asof)
        if not pts:
            return None
        (d, v), prev = pts[-1], (pts[-2][1] if len(pts) > 1 else None)
        change, cu = _delta(meta, v, prev)
        return {"value": v, "change": change, "change_unit": cu, "obs_date": d, "kind": "daily",
                "provider": "derived", "src": layout.PROVIDER_LABEL["derived"], "lag": ""}
    rows = _daily(conn, key, asof, limit=2)
    scale = _scale(key)
    last = rows[-1] if rows else None
    prev_row = rows[-2] if len(rows) > 1 else None
    kind, obs_ts, provider = "daily", "", (last[2] if last else None)
    value = last[1] * scale if last else None
    obs_date = last[0] if last else None
    prev = prev_row[1] * scale if prev_row else None
    if value is None:
        return None
    change, cu = _delta(meta, value, prev)
    return {"value": value, "change": change, "change_unit": cu, "obs_date": obs_date, "kind": kind, "obs_ts": obs_ts,
            "provider": provider, "src": layout.PROVIDER_LABEL.get(provider, provider),
            "lag": layout.PROVIDER_LAG.get(provider, "")}


def tiles(conn: sqlite3.Connection, tab: str, asof: str | None, closes: dict | None = None) -> dict:
    spec = layout.TABS[tab]
    groups = []
    for g in spec["groups"]:
        items = []
        for key in g["tiles"]:
            ref = asof_for(key, asof, closes)
            items.append({**meta_of(key), "state": tile_state(conn, key, ref), "ref_date": None if (key in BY_KEY and BY_KEY[key].step) else ref, "region": region_of(key)})
        groups.append({"id": g["id"], "label": g["label"], "tiles": items})
    return {"tab": tab, "groups": groups}


def visible_keys(tab: str) -> list[str]:
    return [k for g in layout.TABS[tab]["groups"] for k in g["tiles"]]


def _norm(text: str) -> str:
    """검색용 정규화: 소문자, 유니코드 마이너스(−)를 하이픈으로, 공백 정리."""
    return " ".join(text.lower().replace("−", "-").split())


def _search_universe():
    """검색 대상 = 화면에 나오는 지표(레이아웃 순서) + 저장돼 있지만 화면에는 안 나오는 지표(정의된 시리즈·파생값 전부).
    (키, 탭, 탭 이름, 그룹 id|None, 그룹 이름, 화면에 나오는가, 출처 이름)"""
    seen: set[str] = set()
    for tab, spec in layout.TABS.items():
        for g in spec["groups"]:
            for key in g["tiles"]:
                seen.add(key)
                yield key, tab, spec["label"], g["id"], g["label"], True, _source_name(key)
    for key, s in BY_KEY.items():
        if key not in seen:
            seen.add(key)
            yield key, s.tab, layout.TABS[s.tab]["label"], None, "", False, _source_name(key)
    for key, d in derived.REGISTRY.items():
        if key not in seen:
            yield key, d.tab, layout.TABS[d.tab]["label"], None, "", False, _source_name(key)


def _source_name(key: str) -> str:
    if key in derived.REGISTRY:
        return layout.PROVIDER_LABEL["derived"]
    return layout.PROVIDER_LABEL.get(BY_KEY[key].provider, BY_KEY[key].provider)


def all_keys() -> list[str]:
    """차트에 올릴 수 있는 모든 지표 키 (화면 타일 + 화면에 없는 저장 지표)."""
    return [u[0] for u in _search_universe()]


def search_series(conn: sqlite3.Connection, q: str, asof: str | None = None, limit: int = 20,
                  closes: dict | None = None) -> list[dict]:
    """지표 검색. 라벨·키·그룹·탭·출처 이름에서 공백으로 나눈 낱말이 모두 들어 있는 것을 찾는다.
    순위: 라벨에서 맞은 것 → 화면에 나오는 것 → 레이아웃/정의 순서. 값은 asof(없으면 최신)까지의 일별 종가 기준이다.
    정의에서 빠진 항목(USD 스왑·신용 섹터)은 DB 에 행이 없고 정의도 없어 나오지 않는다."""
    tokens = _norm(q).split()
    if not tokens:
        return []
    hits: list[tuple[int, int, int, dict]] = []
    for order, (key, tab, tab_label, gid, glabel, shown, src) in enumerate(_search_universe()):
        m = meta_of(key)
        label = _norm(m["label"])
        hay = " ".join([label, key.lower(), _norm(glabel), _norm(tab_label), _norm(src)])
        if all(t in hay for t in tokens):
            rank = 0 if all(t in label for t in tokens) else 1
            hits.append((rank, 0 if shown else 1, order, {
                "key": key, "label": m["label"], "tab": tab, "tab_label": tab_label, "group": gid, "group_label": glabel,
                "shown": shown, "unit": m["unit"], "decimals": m["decimals"], "manual": m["manual"]}))
    hits.sort(key=lambda h: h[:3])
    out = []
    for *_, item in hits[:limit]:
        st = tile_state(conn, item["key"], asof_for(item["key"], asof, closes))
        item["state"] = ({"value": st["value"], "obs_date": st["obs_date"], "src": st["src"]} if st else None)
        out.append(item)
    return out


def series_points(conn: sqlite3.Connection, key: str, asof: str | None) -> dict:
    meta = meta_of(key)
    if meta["derived"]:
        pts = derived.series(conn, key, end=asof)
        dates, values, provider = [p[0] for p in pts], [p[1] for p in pts], "derived"
    else:
        rows = _daily(conn, key, asof)
        scale = _scale(key)
        dates, values = [r[0] for r in rows], [r[1] * scale for r in rows]
        provider = rows[-1][2] if rows else (BY_KEY[key].provider if key in BY_KEY else "")
    return {**meta, "provider": provider, "src": layout.PROVIDER_LABEL.get(provider, provider),
            "lag": layout.PROVIDER_LAG.get(provider, ""), "dates": dates, "values": values}


def series_many(conn: sqlite3.Connection, keys: list[str], asof: str | None, closes: dict | None = None) -> dict:
    return {k: series_points(conn, k, asof_for(k, asof, closes)) for k in keys if k in BY_KEY or k in derived.REGISTRY}


def _value_at(conn: sqlite3.Connection, key: str, day: str) -> tuple[str, float] | None:
    """day 이전(포함) CURVE_LOOKBACK_DAYS 안에서 가장 가까운 일별 값. (관측일, 값)."""
    start = (date.fromisoformat(day) - timedelta(days=layout.CURVE_LOOKBACK_DAYS)).isoformat()
    if key in derived.REGISTRY:
        pts = derived.series(conn, key, start=start, end=day)
        return pts[-1] if pts else None
    row = conn.execute("SELECT obs_date, value FROM observation WHERE series_key=? AND kind='daily' "
                       "AND obs_date BETWEEN ? AND ? ORDER BY obs_date DESC LIMIT 1", (key, start, day)).fetchone()
    return (row["obs_date"], row["value"]) if row else None


def tenor_label(key: str) -> str:
    if key in BY_KEY and BY_KEY[key].tenor_months:
        m = BY_KEY[key].tenor_months
        return f"{m}M" if m < 12 else f"{m // 12}Y"
    m = re.search(r"_(\d+)([my])_", key + "_") or re.search(r"_(\d+)([my])", key)
    return (m.group(1) + m.group(2).upper()) if m else key


def curve(conn: sqlite3.Connection, family: str, dates: list[str]) -> dict:
    fam = layout.CURVES[family]
    out: dict[str, dict] = {}
    for d in dates:
        vals: list[float | None] = []
        obs: list[str | None] = []
        for key in fam.keys:
            hit = _value_at(conn, key, d)
            vals.append(hit[1] if hit else None)
            obs.append(hit[0] if hit else None)
        out[d] = {"values": vals, "obs_dates": obs}
    return {"family": family, "label": fam.label, "unit": fam.unit, "src": fam.src,
            "tenors": [tenor_label(k) for k in fam.keys], "keys": list(fam.keys), "curves": out}


def last_obs_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(obs_date) FROM observation WHERE kind='daily'").fetchone()
    return row[0] if row and row[0] else None


def curve_default_date(conn: sqlite3.Connection, family: str, asof: str | None) -> str | None:
    """패밀리 구성 값 중 가장 최근 일별 관측일 (asof 이하)."""
    fam = layout.CURVES[family]
    best = None
    for key in fam.keys:
        if key in derived.REGISTRY:
            pts = derived.recent(conn, key, end=asof, need=1)
            d = pts[-1][0] if pts else None
        else:
            rows = _daily(conn, key, asof, limit=1)
            d = rows[0][0] if rows else None
        if d and (best is None or d > best):
            best = d
    return best


def latest_date(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT MAX(obs_date) FROM observation WHERE series_key=? AND kind='daily'", (key,)).fetchone()
    return row[0] if row and row[0] else None


def validate_date(raw: str | None) -> str | None:
    return canonical_date(raw) if raw else None


def default_today() -> str:
    return today_kst().isoformat()


CLOSE_COVERAGE = 0.6      # 권역 지표 중 이만큼 이상이 같은 날 일별 값을 가진 가장 최근 날 = 그 권역의 마감일
CLOSE_LOOKBACK_DAYS = 14
KR_PROVIDERS = {"ecos", "krx", "smbs", "manual"}      # 나머지(FRED·Yahoo)는 미국 권역


def region_of(key: str) -> str:
    """지표의 권역: 'kr'(한국 소스) | 'us'(FRED·Yahoo). 파생값은 구성 값 다수결."""
    if key in derived.REGISTRY:
        regions = [region_of(k) for k in derived.REGISTRY[key].inputs]
        return max(set(regions), key=regions.count)
    return "kr" if key in BY_KEY and BY_KEY[key].provider in KR_PROVIDERS else "us"


def common_close_dates(conn: sqlite3.Connection) -> dict[str, str | None]:
    """권역별 마감일. 한국 소스(ECOS·KRX·smbs)와 미국 소스(FRED·Yahoo)는 게시 시점이 달라(미국은 하루 이틀 늦다)
    하나의 날짜로 맞추면 한쪽의 최신 값을 버리게 된다. 그래서 권역마다 '화면 지표(파생·수동 제외)의 60% 이상이
    일별 값을 가진 가장 최근 날짜'를 구한다. 기준일 모드(마감)의 기본 화면은 권역 마감일의 확정값만 쓰고 장중 값은 쓰지 않는다."""
    keys = sorted({k for tab in layout.TABS for k in visible_keys(tab)
                   if k in BY_KEY and BY_KEY[k].provider != "manual" and not BY_KEY[k].step})
    since = (today_kst() - timedelta(days=CLOSE_LOOKBACK_DAYS)).isoformat()
    out: dict[str, str | None] = {}
    for region in ("kr", "us"):
        ks = [k for k in keys if region_of(k) == region]
        if not ks:
            out[region] = None
            continue
        marks = ",".join("?" * len(ks))
        rows = conn.execute(
            f"SELECT obs_date, COUNT(DISTINCT series_key) n FROM observation WHERE kind='daily' AND obs_date>=? "
            f"AND obs_date<=? AND series_key IN ({marks}) GROUP BY obs_date ORDER BY obs_date DESC",
            [since, default_today(), *ks]).fetchall()
        need = max(1, round(len(ks) * CLOSE_COVERAGE))
        # 60% 를 채운 날이 없으면(수집 초기·소스 장애) 값이 있는 가장 최근 날로 둔다. None 이면 장중 값이 섞여 들어온다
        out[region] = next((r["obs_date"] for r in rows if r["n"] >= need), rows[0]["obs_date"] if rows else None)
    return out


def asof_for(key: str, asof: str | None, closes: dict | None) -> str | None:
    """직접 고른 기준일이 있으면 그것, 없고 마감 모드(closes)면 그 지표 권역의 마감일, 아니면 None(최신·장중 포함)."""
    return asof or ((closes or {}).get(region_of(key)))
