-- MarketView 004: 일일 리포트 실행 기록. 리포트 파일은 파생 결과물이고 DB 가 원본이다.
-- 같은 날짜를 다시 만들면 덮어쓰지 않고 version 을 올린다 (-v2, -v3 ...). 이 테이블도 행을 지우거나 고치지 않는다
-- (drive_status 만 Drive 복사 결과가 나온 뒤 한 번 채운다).

CREATE TABLE report_run (
  id            INTEGER PRIMARY KEY,
  report_date   TEXT NOT NULL,                 -- YYYY-MM-DD (리포트 기준일)
  version       INTEGER NOT NULL,              -- 1, 2, 3 ...
  md_path       TEXT,
  pdf_path      TEXT,                          -- PDF 변환에 실패하면 NULL (Markdown 은 남는다)
  pdf_status    TEXT NOT NULL DEFAULT 'pending' CHECK (pdf_status IN ('pending','ok','failed')),
  drive_status  TEXT NOT NULL DEFAULT 'pending' CHECK (drive_status IN ('pending','ok','failed','skipped')),
  message       TEXT,                          -- 실패 사유 (PDF/Drive/차트). 비밀값은 넣지 않는다
  created_at    TEXT NOT NULL,
  UNIQUE (report_date, version)
);
