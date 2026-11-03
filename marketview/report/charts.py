"""리포트 차트 PNG (matplotlib, 헤드리스). 팔레트는 config/palette.py (화면과 공유), 한글은 맑은 고딕.

데이터가 없으면 None 을 돌려주고 파일을 만들지 않는다. 일부 점이 없으면 그 점만 비운다(선을 0 으로 잇지 않는다).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from .. import derived, queries  # noqa: E402
from ..config import layout, palette  # noqa: E402
from ..config.series import BY_KEY  # noqa: E402

_FONT_FILES = (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\malgunbd.ttf")
_font_ok = False
for _f in _FONT_FILES:
    if Path(_f).exists():
        font_manager.fontManager.addfont(_f)
        _font_ok = True
if _font_ok:
    plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams.update({"axes.unicode_minus": False, "axes.edgecolor": palette.LINE, "axes.labelcolor": palette.MUTED,
                     "xtick.color": palette.MUTED, "ytick.color": palette.MUTED, "text.color": palette.TEXT,
                     "axes.spines.top": False, "axes.spines.right": False, "savefig.facecolor": "white"})


def font_available() -> bool:
    return _font_ok


def _new(width: float = 7.2, height: float = 3.1):
    fig, ax = plt.subplots(figsize=(width, height), dpi=150)
    ax.grid(axis="y", color=palette.LINE, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def _save(fig, ax, path: Path, source: str) -> None:
    ax.legend(frameon=False, fontsize=8, loc="best")
    fig.text(0.995, 0.01, source, ha="right", va="bottom", fontsize=6.5, color=palette.MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def _window(conn: sqlite3.Connection, key: str, day: str, days: int) -> tuple[list[date], list[float], str]:
    pts = queries.series_points(conn, key, day)
    lo = (date.fromisoformat(day) - timedelta(days=days)).isoformat()
    pairs = [(date.fromisoformat(d), v) for d, v in zip(pts["dates"], pts["values"]) if d >= lo]
    return [p[0] for p in pairs], [p[1] for p in pairs], _source(key, pts)


def _source(key: str, pts: dict) -> str:
    """파생값은 구성 값의 출처를 풀어 쓴다 ('파생' 만으로는 어디서 왔는지 알 수 없다)."""
    if key not in derived.REGISTRY:
        return pts["src"]
    provs = {BY_KEY[k].provider for k in derived.REGISTRY[key].inputs if k in BY_KEY}
    return "파생(" + "·".join(sorted(layout.PROVIDER_LABEL.get(p, p) for p in provs)) + ")"


def curve_chart(conn: sqlite3.Connection, family: str, day: str, path: Path) -> str | None:
    default = queries.curve_default_date(conn, family, day)
    if default is None:
        return None
    d0 = date.fromisoformat(default)
    wanted = [(default, "당일"), ((d0 - timedelta(days=7)).isoformat(), "1주 전"), ((d0 - timedelta(days=30)).isoformat(), "1개월 전")]
    data = queries.curve(conn, family, [d for d, _ in wanted])
    fig, ax = _new()
    drawn = 0
    for i, (d, label) in enumerate(wanted):
        c = data["curves"][d]
        xs = [j for j, v in enumerate(c["values"]) if v is not None]
        ys = [c["values"][j] for j in xs]
        if not xs:
            continue
        obs = max(o for o in c["obs_dates"] if o)
        ax.plot(xs, ys, marker="o", markersize=3.5, linewidth=1.6, color=palette.SERIES[i],
                label=f"{label} ({obs})")
        drawn += 1
    if not drawn:
        plt.close(fig)
        return None
    ax.set_xticks(range(len(data["tenors"])), data["tenors"], fontsize=8)
    ax.set_ylabel("%", fontsize=8)
    ax.set_title(f"{data['label']} 커브", loc="left", fontsize=10)
    _save(fig, ax, path, f"출처: {data['src']}")
    return path.name


def align_and_rebase(windows: list[tuple]) -> list[tuple]:
    """[(순번, 키, 날짜들, 값들, 출처)] 를 모든 시리즈가 값을 가진 가장 이른 날부터로 자르고 각 시리즈의 첫 값을 100 으로 맞춘다.
    시작일이 시리즈마다 다르면(휴장일·게시 지연) '시작일=100' 비교가 어긋난다. 점이 2개 미만으로 남은 시리즈는 뺀다."""
    if not windows:
        return []
    common = max(w[2][0] for w in windows)
    out = []
    for i, key, xs, ys, src in windows:
        pairs = [(x, y) for x, y in zip(xs, ys) if x >= common]
        if len(pairs) >= 2 and pairs[0][1]:
            out.append((i, key, [p[0] for p in pairs], [p[1] / pairs[0][1] * 100 for p in pairs], src))
    return out


def lines_chart(conn: sqlite3.Connection, keys: list[str], day: str, days: int, path: Path, title: str, ylabel: str,
                rebase: bool = False) -> str | None:
    fig, ax = _new()
    srcs: set[str] = set()
    drawn = 0
    windows = [(i, key, *_window(conn, key, day, days)) for i, key in enumerate(keys)]
    windows = [w for w in windows if len(w[2]) >= 2]
    if rebase:
        windows = align_and_rebase(windows)
    for i, key, xs, ys, src in windows:
        ax.plot(xs, ys, linewidth=1.6, color=palette.SERIES[i % len(palette.SERIES)], label=queries.label_of(key).replace("−", "-"))   # 맑은 고딕에 U+2212 글리프 없음
        srcs.add(src)
        drawn += 1
    if not drawn:
        plt.close(fig)
        return None
    ax.set_ylabel(ylabel, fontsize=8)
    ax.set_title(title, loc="left", fontsize=10)
    loc = mdates.AutoDateLocator(minticks=3, maxticks=7)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    _save(fig, ax, path, "출처: " + ", ".join(sorted(s for s in srcs if s)) + f" · 기준일 {day}")
    return path.name


def make_all(conn: sqlite3.Connection, day: str, out_dir: Path, stem: str) -> tuple[dict[str, str | None], list[str]]:
    """6개 차트를 만든다. 하나가 실패해도 나머지는 계속한다. (이름→파일명 또는 None, 메모)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    if not _font_ok:
        notes.append("맑은 고딕 폰트를 찾지 못해 차트 한글이 깨질 수 있음")
    jobs = {
        "ktb_curve": lambda p: curve_chart(conn, "ktb", day, p),
        "ust_curve": lambda p: curve_chart(conn, "ust", day, p),
        "spreads": lambda p: lines_chart(conn, ["ktb_10s3s", "ktb_30s10s", "ust_10s2s", "ust_30s10s"], day, 92, p,
                                         "주요 스프레드", "bp"),
        "credit": lambda p: lines_chart(conn, ["corp_aa3_spread_3y", "corp_bbb3_spread_3y"], day, 92, p,
                                        "신용 스프레드 (국고 같은 만기 대비, 3Y)", "bp"),
        "fx": lambda p: lines_chart(conn, ["usdkrw", "usdjpy", "eurusd", "dxy"], day, 31, p, "외환", "시작일=100", rebase=True),
        "equity": lambda p: lines_chart(conn, ["kospi", "sp500", "nasdaq", "russell2000"], day, 31, p, "주가지수", "시작일=100",
                                        rebase=True),
    }
    result: dict[str, str | None] = {}
    for name, job in jobs.items():
        try:
            result[name] = job(out_dir / f"{stem}_{name}.png")
            if result[name] is None:
                notes.append(f"{name}: 그릴 데이터 없음")
        except Exception as exc:  # noqa: BLE001 - 차트 하나의 실패가 리포트를 막지 않는다
            plt.close("all")
            result[name] = None
            notes.append(f"{name}: 생성 실패 ({type(exc).__name__}: {str(exc)[:80]})")
    return result, notes
