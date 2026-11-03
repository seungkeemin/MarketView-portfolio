-- MarketView 002: 이벤트 · 뷰 메모 · 포지션. *_log 는 append-only (UPDATE/DELETE 하지 않는다).
-- 로그는 변경 '후'의 전체 상태를 JSON/본문으로 남긴다 → 로그만 읽어도 전체 이력이 재구성된다.

CREATE TABLE event (
  id         INTEGER PRIMARY KEY,
  ext_key    TEXT UNIQUE,                 -- 동기화 항목의 안정 키 (fomc:2026-10-28, fred:10:2026-10-14). 수동 항목은 NULL
  kind       TEXT NOT NULL,               -- fomc | bok | us_cpi | us_jobs | us_pce | us_gdp | auction | kr_cpi | other
  title      TEXT NOT NULL,
  region     TEXT NOT NULL CHECK (region IN ('KR','US','OTHER')),
  importance INTEGER NOT NULL DEFAULT 2 CHECK (importance BETWEEN 1 AND 3),
  local_date TEXT NOT NULL,               -- 발표 지역 현지 날짜 (KST 변환은 조회 시 서머타임을 반영해 계산한다)
  local_time TEXT,                        -- HH:MM. NULL = 시각 미확인 (추측하지 않는다)
  tz         TEXT NOT NULL,               -- IANA 시간대
  consensus  TEXT,
  memo       TEXT,
  source_url TEXT,
  verified_on TEXT,                       -- 출처를 확인한 날짜
  origin     TEXT NOT NULL CHECK (origin IN ('official','fred','manual')),
  status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','deleted','stale')),
                                          -- deleted = 사용자가 지움(동기화가 되살리지 않음), stale = 출처 목록에서 사라짐
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_event_date ON event(local_date);

CREATE TABLE event_log (
  id        INTEGER PRIMARY KEY,
  event_id  INTEGER NOT NULL,
  action    TEXT NOT NULL CHECK (action IN ('insert','update','delete','stale','revive')),
  snapshot  TEXT NOT NULL,                -- JSON
  logged_at TEXT NOT NULL
);
CREATE INDEX idx_event_log ON event_log(event_id);

CREATE TABLE view_note (
  id         INTEGER PRIMARY KEY,
  note_date  TEXT NOT NULL,
  tag        TEXT NOT NULL CHECK (tag IN ('채권','외환','신용','주식')),
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (note_date, tag)
);

CREATE TABLE view_note_log (
  id        INTEGER PRIMARY KEY,
  note_id   INTEGER NOT NULL,
  note_date TEXT NOT NULL,
  tag       TEXT NOT NULL,
  body      TEXT NOT NULL,
  logged_at TEXT NOT NULL
);
CREATE INDEX idx_note_log ON view_note_log(note_id);

CREATE TABLE position (
  id          INTEGER PRIMARY KEY,
  product     TEXT NOT NULL,              -- 표시 이름 (자유 입력 가능)
  series_key  TEXT,                       -- 현재값이 수집되는 상품이면 series_def/파생 키. NULL = 손익 자동 계산 안 함
  side        TEXT NOT NULL CHECK (side IN ('long','short')),   -- 레벨 기준: long = 레벨 상승 시 이익
  entry_level REAL NOT NULL,              -- 해당 항목의 표시 단위 (금리 %, 스프레드 bp, 환율 원 ...)
  entry_date  TEXT NOT NULL,
  target      REAL,
  stop        REAL,
  rationale   TEXT,
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  exit_level  REAL,
  exit_date   TEXT,
  deleted     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE position_log (
  id          INTEGER PRIMARY KEY,
  position_id INTEGER NOT NULL,
  action      TEXT NOT NULL CHECK (action IN ('insert','update','close','delete')),
  snapshot    TEXT NOT NULL,              -- JSON
  logged_at   TEXT NOT NULL
);
CREATE INDEX idx_position_log ON position_log(position_id);
