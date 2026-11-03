import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marketview import db, news
from marketview.collect import rss, telegram
from marketview.collect.base import Failed
from marketview.config import news as ncfg
from marketview.config import telegram as tcfg
from marketview.config.news import Feed

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)      # = 2026-10-10 00:00 KST


def rss_xml(*items, extra=""):
    body = "".join(f"<item>{i}</item>" for i in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>{extra}{body}</channel></rss>'.encode()


def item(title, link, pub, desc="", src=""):
    return (f"<title>{title}</title><link>{link}</link><pubDate>{pub}</pubDate>"
            + (f"<description>{desc}</description>" if desc else "") + (f"<source>{src}</source>" if src else ""))


# --- 날짜 ----------------------------------------------------------------------
@pytest.mark.parametrize("raw,tz,expect", [
    ("Fri, 09 Oct 2026 22:34:53 +09:00", "Asia/Seoul", "2026-10-09T22:34:53+09:00"),     # 콜론 있는 오프셋 (매일경제)
    ("Fri, 09 Oct 2026 22:37:24 +0900", "Asia/Seoul", "2026-10-09T22:37:24+09:00"),
    ("Fri, 9 Oct 2026 18:20:24 +0900", "Asia/Seoul", "2026-10-09T18:20:24+09:00"),       # 0 패딩 없는 날 (연합뉴스)
    ("Thu, 08 Oct 2026 06:41:51 GMT", "Asia/Seoul", "2026-10-08T15:41:51+09:00"),         # 구글 뉴스
    ("2026-10-09 23:43:44", "Asia/Seoul", "2026-10-09T23:43:44+09:00"),                  # 시간대 없음: 인포맥스 = KST
    ("2026-10-09 14:44:09", "UTC", "2026-10-09T23:44:09+09:00"),                          # 시간대 없음: Investing = UTC
    ("2026-10-09T14:44:09Z", "Asia/Seoul", "2026-10-09T23:44:09+09:00"),
])
def test_parse_date(raw, tz, expect):
    assert rss.parse_date(raw, tz).astimezone(rss.KST).isoformat() == expect


def test_unparseable_date_is_none_not_now():
    assert rss.parse_date("어제 오후", "Asia/Seoul") is None and rss.parse_date("", "Asia/Seoul") is None


# --- 정리 ----------------------------------------------------------------------
def test_clean_text_double_escaped_and_tags():
    assert rss.clean_text("&amp;quot;오늘도 부캉이&amp;quot;…") == '"오늘도 부캉이"…'
    assert rss.clean_text("<table><tr><td><img src=x>본문 <b>강조</b></td></tr></table>&nbsp;끝") == "본문 강조 끝"
    assert rss.clean_text("가" * 600, 400).endswith("…") and len(rss.clean_text("가" * 600, 400)) == 400


def test_canonical_link_strips_tracking_keeps_identity():
    a = rss.canonical_link("https://News.Example.com/a/1/?utm_source=x&idxno=44&fbclid=zz#top")
    assert a == "https://news.example.com/a/1?idxno=44"
    assert rss.canonical_link("https://news.example.com/a/1?idxno=44") == a


def test_parse_feed_google_news_strips_outlet_and_summary():
    feed = Feed("gn", "구글뉴스", "http://x", "bond", summary=False)
    xml = rss_xml(item("코스피 2.62% 하락 마감 - 동아일보", "https://news.google.com/rss/articles/CBM1?oc=5", "Thu, 08 Oct 2026 06:41:51 GMT",
                       "<ol><li>관련</li></ol>", "동아일보"))
    [it] = rss.parse_feed(xml, feed, NOW)
    assert (it.title, it.source, it.summary) == ("코스피 2.62% 하락 마감", "동아일보", None)


def test_parse_feed_skips_broken_items_and_clamps_future_time():
    feed = Feed("x", "X", "http://x")
    xml = rss_xml(item("제목 없음 링크", "", "Fri, 09 Oct 2026 10:00:00 +0900"), item("", "http://a/1", "Fri, 09 Oct 2026 10:00:00 +0900"),
                  item("날짜 없음", "http://a/2", ""), item("미래 시각", "http://a/3", "Sat, 10 Oct 2026 09:00:00 +0900"),
                  item("정상", "http://a/4", "Fri, 09 Oct 2026 10:00:00 +0900", "<p>요약</p>"))
    got = rss.parse_feed(xml, feed, NOW)
    assert [g.title for g in got] == ["미래 시각", "정상"]
    assert got[0].published_at == "2026-10-10T00:00:00+09:00"                  # 시계 오류로 미래인 값은 현재로 눌러 둔다
    assert got[1].summary == "요약"


def test_parse_feed_atom():
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>아톰 기사</title><link href="http://a/9"/>'
            '<updated>2026-10-09T10:00:00+09:00</updated><summary>요약문</summary></entry></feed>').encode()
    [it] = rss.parse_feed(atom, Feed("a", "A", "http://x"), NOW)
    assert (it.title, it.link, it.published_at, it.summary) == ("아톰 기사", "http://a/9", "2026-10-09T10:00:00+09:00", "요약문")


def test_fetch_feed_failure_is_failed_not_exception(monkeypatch):
    monkeypatch.setattr(rss, "get", lambda url, **kw: (_ for _ in ()).throw(RuntimeError("HTTP 404")))
    res = rss.fetch_feed(Feed("x", "X", "http://x"))
    assert isinstance(res, Failed) and "404" in res.message
    class R:
        content = b"<html>not xml"
    monkeypatch.setattr(rss, "get", lambda url: R())
    assert isinstance(rss.fetch_feed(Feed("x", "X", "http://x")), Failed)


# --- 분류 ----------------------------------------------------------------------
@pytest.mark.parametrize("title,summary,default,expect", [
    ("국고채 3년 금리 2.2bp 상승", None, None, "bond"),
    ("원/달러 환율 4.3원 오른 1,342.8원", None, None, "fx"),
    ("회사채 AA- 스프레드 확대, 여전채 발행 급증", None, None, "credit"),
    ("코스피 2.62% 하락…외국인 순매도", None, None, "equity"),
    ("펭귄 솔루션스 투자의견 매수", "주가가 40달러까지 오를 것", None, None),                 # 요약의 '달러' 만으로는 분류하지 않는다
    ("일본 위안부 문제도 질타", None, None, None),                                           # '위안' 부분문자열 오분류 방지
    ("기안84 크레딧 필요 없다더니", None, None, None),                                       # 연예 기사의 크레딧
    ("이번 주 일정 정리", None, "bond", "bond"),                                             # 기본 탭이 있는 피드는 단서가 없어도 그 탭
    ("코스피 급락에 환율 상승", None, "equity", "equity"),                                    # 기본 탭 가산점이 동점 단서를 이긴다
    ("코스피 급락에 원/달러 환율 급등", None, "equity", "fx"),                                # 외환 단서가 더 많으면 외환
    ("원/달러 환율 1,340원, 국고채 금리 상승", None, None, "fx"),                            # 동점이면 외환 > 채권
])
def test_classify(title, summary, default, expect):
    assert news.classify(title, summary, default) == expect


def test_title_key_matches_same_headline_across_formatting():
    assert news.title_key("[외환] 원/달러 환율 4.3원 오른 1,342.8원(15:30 기준가)") == news.title_key("[외환]  원/달러 환율 4.3원 오른 1,342.8원 (15:30 기준가)")


# --- 저장 · 중복 · 검색 ----------------------------------------------------------
def mk(link, title, feed="f", pub="2026-10-09T10:00:00+09:00", summary=None, source="언론"):
    return rss.RawItem(feed, link, title, summary, source, pub)


def test_store_dedupes_by_link_and_is_idempotent(conn):
    items = [mk("http://a/1", "국고채 금리 상승"), mk("http://a/2", "환율 하락")]
    assert news.store_items(conn, items, feed_tabs={"f": None}) == 2
    assert news.store_items(conn, items, feed_tabs={"f": None}) == 0
    assert conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 2


def test_same_story_from_two_feeds_is_shown_once(conn):
    news.store_items(conn, [mk("http://a/1", "국고채 금리 상승 [종합]", "f1", "2026-10-09T10:00:00+09:00", source="A")], feed_tabs={"f1": None})
    news.store_items(conn, [mk("http://b/9", "국고채 금리 상승 [종합]", "f2", "2026-10-09T10:05:00+09:00", source="B")], feed_tabs={"f2": None})
    assert conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 2                 # 저장은 링크 기준
    shown = news.feed(conn, "bond", asof="2026-10-09")
    assert len(shown) == 1 and shown[0]["source"] == "A"                                  # 화면은 먼저 저장된 것 하나


def test_feed_orders_newest_first_filters_tab_and_asof(conn):
    news.store_items(conn, [mk("http://a/1", "국고채 금리 하락", pub="2026-10-07T09:00:00+09:00"),
                            mk("http://a/2", "국고채 금리 상승", pub="2026-10-09T09:00:00+09:00"),
                            mk("http://a/3", "코스피 급락", pub="2026-10-09T10:00:00+09:00")], feed_tabs={"f": None})
    got = news.feed(conn, "bond", asof="2026-10-09")
    assert [g["title"] for g in got] == ["국고채 금리 상승", "국고채 금리 하락"]
    assert [g["title"] for g in news.feed(conn, "bond", asof="2026-10-08")] == ["국고채 금리 하락"]        # 과거 기준일: 그날까지만
    assert [g["title"] for g in news.feed(conn, "equity", asof="2026-10-09")] == ["코스피 급락"]


def test_old_news_hidden_by_default_window_but_searchable(conn):
    news.store_items(conn, [mk("http://a/1", "국고채 금통위 의사록 공개", pub="2026-08-01T09:00:00+09:00")], feed_tabs={"f": None})
    assert news.feed(conn, "bond", asof="2026-10-09") == []                                # 14일 창 밖
    assert len(news.feed(conn, None, q="의사록", asof="2026-10-09")) == 1                  # 검색은 전체 기간


def test_search_trigram_and_short_query_and_special_chars(conn):
    news.store_items(conn, [mk("http://a/1", "금통위가 기준금리를 동결했다", summary="이창용 총재 기자간담회"),
                            mk("http://a/2", "S&P 500 \"사상 최고\" 경신", summary=None),
                            mk("http://a/3", "100% 확신은 아니다", summary=None)], feed_tabs={"f": None})
    assert len(news.feed(conn, None, q="기준금리", asof="2026-10-09")) == 1             # 단어 중간 부분일치
    assert len(news.feed(conn, None, q="간담회", asof="2026-10-09")) == 1               # 요약에서도
    assert len(news.feed(conn, None, q="금통", asof="2026-10-09")) == 1                 # 2글자 → LIKE
    assert len(news.feed(conn, None, q='"사상 최고"', asof="2026-10-09")) == 1          # 따옴표가 FTS 문법을 깨지 않는다
    assert len(news.feed(conn, None, q="S&P", asof="2026-10-09")) == 1
    assert len(news.feed(conn, None, q="%", asof="2026-10-09")) == 1                   # % 는 와일드카드가 아니다
    assert news.feed(conn, None, q="없는단어", asof="2026-10-09") == []


def test_collect_rss_isolates_failing_feed(conn):
    feeds = (Feed("ok", "OK", "u1", "bond"), Feed("bad", "BAD", "u2"), Feed("empty", "E", "u3"))
    def fake(f):
        if f.id == "bad":
            return Failed("bad", "HTTP 404")
        return [mk("http://a/1", "국고채 금리", feed="ok", pub="2026-10-09T10:00:00+09:00")] if f.id == "ok" else []
    res = {r["feed"]: r for r in news.collect_rss(conn, feeds, fetch=fake, workers=2)}
    assert (res["ok"]["status"], res["ok"]["new"]) == ("ok", 1)
    assert (res["bad"]["status"], res["bad"]["message"]) == ("error", "HTTP 404")
    assert res["empty"]["status"] == "miss"


def test_mark_read(conn):
    news.store_items(conn, [mk("http://a/1", "국고채 금리")], feed_tabs={"f": None})
    nid = conn.execute("SELECT id FROM news").fetchone()["id"]
    news.mark_read(conn, "news", nid)
    assert news.feed(conn, "bond", asof="2026-10-09")[0]["is_read"] is True
    news.mark_read(conn, "news", nid, False)
    assert news.feed(conn, "bond", asof="2026-10-09")[0]["is_read"] is False
    with pytest.raises(LookupError):
        news.mark_read(conn, "news", 9999)
    with pytest.raises(ValueError):
        news.mark_read(conn, "blog", nid)


def test_configured_feeds_are_unique_and_valid():
    ids = [f.id for f in ncfg.FEEDS]
    assert len(ids) == len(set(ids)) and all(f.url.startswith("http") for f in ncfg.FEEDS)
    assert {f.tab for f in ncfg.FEEDS if f.tab} <= set(news.TABS)
    assert set(ncfg.TAB_KEYWORDS) == set(news.TABS)


# --- 텔레그램 ---------------------------------------------------------------------
CH = tcfg.Channel("@research_a", "A증권", "bond")


def raw(msg_id, body, posted="2026-10-09T09:00:00+09:00", edited=None, **kw):
    return telegram.RawMessage("@research_a", "A증권 리서치", msg_id, body, posted, edited_at=edited, **kw)


class FakeSource:
    def __init__(self, new=(), again=(), fail=None):
        self.new, self.again, self.fail, self.calls = list(new), list(again), fail, []

    def fetch(self, channel, min_id, limit, recheck_ids):
        self.calls.append((channel.key, min_id, limit, list(recheck_ids)))
        if self.fail:
            raise self.fail
        return [m for m in self.new if m.msg_id > min_id], list(self.again)


def test_telegram_store_dedupes_and_keeps_original_on_edit(conn):
    src = FakeSource([raw(10, "4Q 채권 전망\n스티프닝 지속", links=("https://x/y",), attachments=({"name": "r.pdf", "mime": "application/pdf", "size": 1, "kind": "pdf"},))])
    res = telegram.collect(conn, src, [CH])
    assert res[0]["status"] == "ok" and res[0]["new"] == 1
    assert telegram.collect(conn, src, [CH])[0]["new"] == 0                                    # 같은 메시지를 다시 받아도 중복 없음
    assert src.calls[1][1] == 10                                                               # 두 번째는 마지막 msg_id 이후만 요청
    src.again = [raw(10, "4Q 채권 전망 (정정)\n스티프닝 지속", edited="2026-10-09T10:00:00+09:00")]
    r = telegram.collect(conn, src, [CH])[0]
    assert r["edits"] == 1
    assert telegram.collect(conn, src, [CH])[0]["edits"] == 0                                  # 같은 수정본은 한 번만
    assert conn.execute("SELECT body FROM tg_message WHERE msg_id=10").fetchone()["body"] == "4Q 채권 전망\n스티프닝 지속"   # 원본 유지
    assert conn.execute("SELECT body FROM tg_message_edit").fetchone()["body"].startswith("4Q 채권 전망 (정정)")
    row = news.feed(conn, "bond", asof="2026-10-09", source="telegram")[0]
    assert row["kind"] == "telegram" and row["title"] == "4Q 채권 전망 (정정)" and row["links"] == ["https://x/y"] and row["attachments"][0]["kind"] == "pdf"
    assert row["link"] == "https://t.me/research_a/10" and row["source"].startswith("텔레그램")


def test_telegram_recheck_ids_cover_recent_messages_only(conn):
    src = FakeSource([raw(1, "옛 메시지", posted="2026-01-01T09:00:00+09:00"), raw(2, "최근", posted=telegram._iso_kst(datetime.now(telegram.KST)))])
    telegram.collect(conn, src, [CH])
    telegram.collect(conn, src, [CH])
    assert src.calls[1][3] == [2]


def test_telegram_unconfigured_is_skipped_not_error(conn, monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.setattr(telegram, "env", lambda k, d="": "")
    assert telegram.collect(conn, None, [])[0]["status"] == "skipped"
    r = telegram.collect(conn, None, [CH])[0]
    assert r["status"] == "skipped" and "TELEGRAM_API_ID" in r["message"]


def test_telegram_login_problem_skips_and_other_errors_are_isolated(conn):
    two = [CH, tcfg.Channel("@b", "B")]
    res = telegram.collect(conn, FakeSource(fail=telegram.TelegramUnavailable("로그인이 필요합니다")), two)
    assert [r["status"] for r in res] == ["skipped"]                                           # 채널마다 반복하지 않는다
    res = telegram.collect(conn, FakeSource(fail=RuntimeError("FloodWait")), two)
    assert [r["status"] for r in res] == ["error", "error"] and "FloodWait" in res[0]["message"]


def test_telegram_code_never_calls_write_apis():
    """읽기 전용 보장: 전송·삭제·편집·읽음 처리 호출이 코드에 없어야 한다."""
    root = Path(__file__).resolve().parents[1]
    forbidden = re.compile(r"send_message|send_file|send_read_acknowledge|delete_messages|edit_message|forward_messages|"
                           r"SendMessageRequest|DeleteMessagesRequest|ReadHistoryRequest|pin_message|\.action\(|send_reaction")
    for path in [root / "marketview/collect/telegram.py", root / "scripts/telegram_login.py"]:
        assert not forbidden.search(path.read_text(encoding="utf-8")), path


def test_secrets_and_session_are_gitignored():
    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "*.session" in ignore and ".env" in ignore


# --- HTTP -------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "n.db"
    monkeypatch.setenv("MARKETVIEW_DB_PATH", str(path))
    c = db.connect(path)
    db.migrate(c)
    news.store_items(c, [mk("http://a/1", "금통위 앞두고 국고채 금리 약보합", pub="2026-10-09T09:00:00+09:00", summary="동결 전망")], feed_tabs={"f": "bond"})
    c.close()
    from marketview.app import app
    return TestClient(app)


def test_http_news(client, monkeypatch):
    monkeypatch.setattr(tcfg, "CHANNELS", ())                                       # 실제 설정과 무관하게 '채널 없음' 상태를 시험
    r = client.get("/api/news", params={"tab": "bond", "asof": "2026-10-09"}).json()
    assert r["items"][0]["title"].startswith("금통위") and "금통위" in r["keywords"] and r["telegram"]["state"] == "no_channels"
    nid = r["items"][0]["id"]
    assert client.post(f"/api/news/news/{nid}/read", json={"read": True}).status_code == 200
    assert client.get("/api/news", params={"tab": "bond", "asof": "2026-10-09"}).json()["items"][0]["is_read"] is True
    assert client.post("/api/news/news/9999/read", json={}).status_code == 404
    assert client.post(f"/api/news/blog/{nid}/read", json={}).status_code == 400
    assert client.get("/api/news", params={"tab": "nope"}).status_code == 404
    assert client.get("/api/news", params={"source": "x"}).status_code == 400
    assert client.get("/api/news", params={"asof": "2026-1-1"}).status_code == 400
    assert len(client.get("/api/news", params={"q": "금통", "asof": "2026-10-09"}).json()["items"]) == 1


def test_login_script_uses_async_telethon_correctly():
    """Telethon 은 비동기다: 동기식 with 로 쓰면 get_me() 가 코루틴을 돌려줘 AttributeError 가 난다 (2026-10-10 실제 발생)."""
    text = (Path(__file__).resolve().parents[1] / "scripts/telegram_login.py").read_text(encoding="utf-8")
    assert "await client.get_me()" in text and "async for d in client.iter_dialogs()" in text
    assert not re.search(r"^\s*with TelegramClient", text, re.M)
    assert "asyncio.run(" in text


def test_telegram_attachment_only_message_gets_a_title(conn):
    src = FakeSource([raw(1, "", attachments=({"name": "신한_채권전략.pdf", "mime": "application/pdf", "size": 9, "kind": "pdf"},)),
                      raw(2, "", attachments=({"name": "이미지", "mime": "image/jpeg", "size": None, "kind": "photo"},))])
    telegram.collect(conn, src, [CH])
    titles = sorted(i["title"] for i in news.feed(conn, "bond", asof="2026-10-09", source="telegram"))
    assert titles == ["[PDF] 신한_채권전략.pdf", "[이미지]"]


def test_parse_feed_rejects_non_http_links_and_entities():
    feed = Feed("x", "x", "http://x", "bond", summary=False)
    xml = rss_xml(item("나쁜 링크", "javascript:alert(1)", "Thu, 08 Oct 2026 06:41:51 GMT", "", "x"))
    assert rss.parse_feed(xml, feed, NOW) == []
    with pytest.raises(ValueError):
        rss.parse_feed(b'<!DOCTYPE r [<!ENTITY a "aaaa">]><rss/>', feed, NOW)
    with pytest.raises(ValueError):
        rss.parse_feed(b"<rss>" + b" " * (rss.MAX_FEED_BYTES + 1) + b"</rss>", feed, NOW)


def test_same_title_on_different_days_is_not_collapsed(conn):
    """매일 같은 제목으로 나오는 시황 기사(링크는 매일 다름)가 첫날 이후 사라지던 버그."""
    day = lambda d: f"2026-10-{d:02d}T15:05:00+09:00"
    news.store_items(conn, [mk(f"http://g/{d}", "Stock market will S&P 500 open up or down today", pub=day(d)) for d in (9, 12, 13)],
                     feed_tabs={"f": "equity"})
    got = news.feed(conn, "equity", asof="2026-10-13")
    assert [g["published_at"][:10] for g in got] == ["2026-10-13", "2026-10-12", "2026-10-09"]


def test_same_story_within_window_collapses_even_across_feeds_but_not_across_tabs(conn):
    """같은 기사가 두 피드에서 서로 다른 탭으로 분류돼도 각 탭에서는 보인다(다른 탭의 먼저 저장된 행이 가리면 안 된다)."""
    news.store_items(conn, [mk("http://a/1", "시황 점검", "f1", "2026-10-09T10:00:00+09:00")], feed_tabs={"f1": "equity"})
    news.store_items(conn, [mk("http://b/1", "시황 점검", "f2", "2026-10-09T10:20:00+09:00")], feed_tabs={"f2": "bond"})
    assert [g["link"] for g in news.feed(conn, "equity", asof="2026-10-09")] == ["http://a/1"]
    assert [g["link"] for g in news.feed(conn, "bond", asof="2026-10-09")] == ["http://b/1"]
    assert len(news.feed(conn, None, q="시황", asof="2026-10-09")) == 1                  # 검색은 탭을 가리지 않으므로 한 번만


def test_dedupe_still_honors_limit(conn):
    news.store_items(conn, [mk(f"http://a/{i}", f"국고채 금리 {i}", pub=f"2026-10-09T{i % 24:02d}:00:00+09:00") for i in range(10)]
                     + [mk(f"http://b/{i}", f"국고채 금리 {i}", "f2", f"2026-10-09T{i % 24:02d}:05:00+09:00") for i in range(10)],
                     feed_tabs={"f": None, "f2": None})
    assert len(news.feed(conn, "bond", asof="2026-10-09", limit=7)) == 7


def test_feed_shows_latest_telegram_edit_and_flags_it(conn):
    src = FakeSource([raw(10, "4Q 채권 전망" + chr(10) + "스티프닝 지속")])
    telegram.collect(conn, src, [CH])
    first = news.feed(conn, "bond", asof="2026-10-09", source="telegram")[0]
    assert first["title"] == "4Q 채권 전망" and first["edited"] is False
    src.again = [raw(10, "4Q 채권 전망 (정정)" + chr(10) + "스티프닝 지속", edited="2026-10-09T10:00:00+09:00")]
    telegram.collect(conn, src, [CH])
    shown = news.feed(conn, "bond", asof="2026-10-09", source="telegram")[0]
    assert shown["title"] == "4Q 채권 전망 (정정)" and shown["edited"] is True
    assert conn.execute("SELECT body FROM tg_message WHERE msg_id=10").fetchone()["body"].startswith("4Q 채권 전망" + chr(10))   # 원본은 그대로


def test_fetch_feed_uses_per_feed_connect_timeout_and_retries(monkeypatch):
    """mt 는 클라우드에서 연결 단계가 막힌다: 실패 한 번이 회차를 70초씩 잡아먹지 않도록 연결 제한·재시도를 줄여 둔다."""
    seen = {}
    def fake_get(url, timeout, retries):
        seen.update(timeout=timeout, retries=retries)
        class R: content = b"<rss><channel></channel></rss>"
        return R()
    monkeypatch.setattr(rss, "get", fake_get)
    mt = next(f for f in ncfg.FEEDS if f.id == "mt")
    rss.fetch_feed(mt)
    assert seen["timeout"].connect == mt.connect_timeout <= 8 and seen["retries"] == mt.retries <= 1
    other = next(f for f in ncfg.FEEDS if f.id == "mk_economy")
    rss.fetch_feed(other)
    assert seen["timeout"].connect == rss.FEED_TIMEOUT.connect and seen["timeout"].read == rss.FEED_TIMEOUT.read and seen["retries"] == rss.FEED_RETRIES
    worst = (mt.connect_timeout * (mt.retries + 1)) + 2 * mt.retries
    assert worst <= 20                                                                         # 실패해도 회차 시간 상한이 짧다


def test_timeout_messages_distinguish_connect_from_read():
    import httpx
    from marketview.collect.base import describe
    assert describe(httpx.ConnectTimeout("x")) == "연결 타임아웃"
    assert describe(httpx.ReadTimeout("x")) == "읽기 타임아웃"
    assert describe(httpx.WriteTimeout("x")) == "타임아웃"
