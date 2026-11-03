-- MarketView 003: 뉴스 · 텔레그램. 기사 본문 전체는 저장하지 않는다 (제목·요약·링크만, 원문은 링크로 연다).
-- 검색은 FTS5 trigram (한국어 단어 중간 검색). 3글자 미만 검색어는 앱에서 LIKE 로 처리한다.

CREATE TABLE news (
  id           INTEGER PRIMARY KEY,
  link         TEXT NOT NULL UNIQUE,                   -- 정규화한 링크가 중복 방지 키
  title        TEXT NOT NULL,
  summary      TEXT,
  source       TEXT NOT NULL,                          -- 언론사/기관 이름
  feed_id      TEXT NOT NULL,
  published_at TEXT NOT NULL,                          -- ISO8601 +09:00
  tab          TEXT CHECK (tab IN ('bond','fx','credit','equity')),   -- NULL = 분류 안 됨 (탭엔 안 보이고 검색엔 나온다)
  title_key    TEXT NOT NULL,                          -- 같은 기사가 여러 피드로 올 때 화면에서 한 번만 보여주기 위한 키
  is_read      INTEGER NOT NULL DEFAULT 0,
  fetched_at   TEXT NOT NULL,
  origin       TEXT NOT NULL DEFAULT 'local' CHECK (origin IN ('local','cloud'))
);
CREATE INDEX idx_news_pub ON news(published_at);
CREATE INDEX idx_news_tab ON news(tab, published_at);
CREATE INDEX idx_news_key ON news(title_key);

CREATE VIRTUAL TABLE news_fts USING fts5(title, summary, content='news', content_rowid='id', tokenize='trigram');
CREATE TRIGGER news_ai AFTER INSERT ON news BEGIN
  INSERT INTO news_fts(rowid, title, summary) VALUES (new.id, new.title, new.summary);
END;
CREATE TRIGGER news_ad AFTER DELETE ON news BEGIN
  INSERT INTO news_fts(news_fts, rowid, title, summary) VALUES ('delete', old.id, old.title, old.summary);
END;
CREATE TRIGGER news_au AFTER UPDATE OF title, summary ON news BEGIN
  INSERT INTO news_fts(news_fts, rowid, title, summary) VALUES ('delete', old.id, old.title, old.summary);
  INSERT INTO news_fts(rowid, title, summary) VALUES (new.id, new.title, new.summary);
END;

CREATE TABLE tg_message (
  id            INTEGER PRIMARY KEY,
  channel       TEXT NOT NULL,                         -- 설정 파일의 채널 키
  channel_title TEXT,
  msg_id        INTEGER NOT NULL,
  body          TEXT NOT NULL DEFAULT '',              -- 최초 수집 본문 (수정돼도 원본은 그대로 둔다)
  posted_at     TEXT NOT NULL,
  links_json    TEXT NOT NULL DEFAULT '[]',
  attachments_json TEXT NOT NULL DEFAULT '[]',         -- [{name, mime, size, kind}]
  tab           TEXT CHECK (tab IN ('bond','fx','credit','equity')),
  is_read       INTEGER NOT NULL DEFAULT 0,
  fetched_at    TEXT NOT NULL,
  UNIQUE (channel, msg_id)
);
CREATE INDEX idx_tg_pub ON tg_message(posted_at);
CREATE INDEX idx_tg_tab ON tg_message(tab, posted_at);

CREATE VIRTUAL TABLE tg_fts USING fts5(body, content='tg_message', content_rowid='id', tokenize='trigram');
CREATE TRIGGER tg_ai AFTER INSERT ON tg_message BEGIN
  INSERT INTO tg_fts(rowid, body) VALUES (new.id, new.body);
END;
CREATE TRIGGER tg_ad AFTER DELETE ON tg_message BEGIN
  INSERT INTO tg_fts(tg_fts, rowid, body) VALUES ('delete', old.id, old.body);
END;

CREATE TABLE tg_message_edit (                         -- 원문이 나중에 수정되면 수정본을 따로 남긴다 (append-only)
  id        INTEGER PRIMARY KEY,
  channel   TEXT NOT NULL,
  msg_id    INTEGER NOT NULL,
  body      TEXT NOT NULL,
  edited_at TEXT NOT NULL,
  seen_at   TEXT NOT NULL
);
CREATE INDEX idx_tg_edit ON tg_message_edit(channel, msg_id);
