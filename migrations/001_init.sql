-- MarketView 001: 시세 레이어. 뉴스·텔레그램·이벤트·메모·포지션은 해당 Phase 에서 새 번호로 추가한다.
-- 규칙: 미수집은 행 부재(0 으로 채우지 않는다) · 파생값은 저장하지 않는다 · *_log 는 append-only.

CREATE TABLE series_def (
  series_key   TEXT PRIMARY KEY,
  label_ko     TEXT NOT NULL,
  tab          TEXT NOT NULL CHECK (tab IN ('bond','fx','credit','equity')),
  group_key    TEXT NOT NULL,
  tenor_months INTEGER,
  unit         TEXT NOT NULL,
  decimals     INTEGER NOT NULL,
  delta_unit   TEXT NOT NULL,
  provider     TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('confirmed','unofficial','manual','unavailable')),
  note         TEXT,
  display_order INTEGER NOT NULL,
  active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE observation (
  series_key  TEXT NOT NULL REFERENCES series_def(series_key),
  obs_date    TEXT NOT NULL,                       -- YYYY-MM-DD, 소스가 알려준 실제 관측일
  kind        TEXT NOT NULL CHECK (kind IN ('daily','intraday')),
  obs_ts      TEXT NOT NULL DEFAULT '',            -- intraday: 관측 시각(KST ISO), daily: ''
  value       REAL NOT NULL,
  provider    TEXT NOT NULL,
  origin      TEXT NOT NULL CHECK (origin IN ('local','cloud','manual','import')),
  captured_at TEXT NOT NULL,                       -- 수집 시각. 소급 수집분은 obs_date 보다 한참 뒤다
  note        TEXT,
  PRIMARY KEY (series_key, obs_date, kind, obs_ts)
) WITHOUT ROWID;
CREATE INDEX idx_obs_date ON observation(obs_date, kind);

CREATE TABLE observation_log (
  id            INTEGER PRIMARY KEY,
  series_key    TEXT NOT NULL,
  obs_date      TEXT NOT NULL,
  kind          TEXT NOT NULL,
  obs_ts        TEXT NOT NULL,
  action        TEXT NOT NULL CHECK (action IN ('insert','update')),
  prev_value    REAL,
  new_value     REAL NOT NULL,
  prev_provider TEXT,
  new_provider  TEXT NOT NULL,
  written_at    TEXT NOT NULL
);
CREATE INDEX idx_obslog ON observation_log(series_key, obs_date);

CREATE TABLE ingest_run (
  id          INTEGER PRIMARY KEY,
  origin      TEXT NOT NULL,
  kind        TEXT NOT NULL,                        -- daily | intraday | backfill
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL CHECK (status IN ('running','ok','partial','failed'))
);

CREATE TABLE ingest_result (
  run_id     INTEGER NOT NULL REFERENCES ingest_run(id),
  source     TEXT NOT NULL,
  series_key TEXT NOT NULL,
  status     TEXT NOT NULL CHECK (status IN ('ok','miss','error','skipped')),
  rows       INTEGER NOT NULL DEFAULT 0,
  message    TEXT,
  PRIMARY KEY (run_id, source, series_key)
);

CREATE TABLE sync_state (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE app_setting (
  key   TEXT PRIMARY KEY,
  value TEXT
);
