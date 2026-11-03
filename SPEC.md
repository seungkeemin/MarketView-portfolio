# SPEC.md — MarketView

> Phase 0 산출물 (초안, 승인 대기). 작성일 2026-10-09.
> 운영 규칙은 [CLAUDE.md](CLAUDE.md). 요구사항 원문은 [MarketView_프롬프트.md](MarketView_프롬프트.md).
> 표기: **확인됨** = 실제 호출로 확인된 것(ficc-desk, 확인일 유지) · **확인 필요** = Phase 1 탐색 스크립트로 호출해 확인하기 전까지 가정하지 않음 · **수동** = 소스 없음, 화면에서 입력.

---

## 1. 목적·범위

외국계 IB 셀사이드 FICC/파생 트레이더 준비생 1인용 로컬 앱. 다섯 가지를 한다.

1. 채권·외환·신용·주식 데이터를 그래프로 본다
2. 국내외 뉴스 + 텔레그램 리서치 메시지를 읽는다
3. 다가오는 매크로 이벤트를 본다
4. 내 뷰와 포지션을 적는다
5. 매일 리포트(Markdown + PDF)로 정리해 노트북과 Google Drive에 남긴다

노트북이 꺼져 있어도 데이터가 끊기지 않고 쌓인다 (§6).

**비범위**: 루틴 체크리스트, 연속 기록 일수, 무효화 워치, 단계 게이트, 인증/다중 사용자, Drive API, 클라우드에서의 리포트 생성(나중에 별도 요청).

**기존 데스크에서 불편했던 점**: 사용자가 설계(프롬프트 §1, §2)에 이미 반영했다고 함 (§13).

## 2. 환경 실측 (2026-10-09)

| 항목 | 결과 |
|---|---|
| OS | Windows 11 Home |
| Python | 3.14 설치됨 (`%LOCALAPPDATA%\Programs\Python\Python314\python.exe`). PATH 기본 `python`은 3.12.10 → **venv는 3.14 경로로 명시 생성** |
| git | 있음 |
| Node.js | 없음 (필요 없음 — 빌드 단계 없음) |
| GitHub CLI (gh) | 없음 → Phase 8에서 설치 승인 요청 |
| winget | 있음 |
| Chrome | `C:\Program Files\Google\Chrome\Application\chrome.exe` 있음 |
| Google Drive 동기화 | `G:\내 드라이브` 존재. 확정 경로 `G:\내 드라이브\MarketView\{reports,backups}` (생성 완료, §13) |
| 옵시디언 볼트 | 로컬 옵시디언 볼트 (선택 기능, 기본 꺼짐) |
| ficc-desk/data/ficc.db | 1,013행 · 41필드 · 2026-08-07~2026-10-09. API 백필이 우선, 이 DB는 보조 import 후보 |
| ficc-desk requirements.txt / .env.example | 한글 주석 인코딩이 깨져 읽힘 → 복사하지 않고 새로 작성 |

## 3. 기술 스택과 근거

| 계층 | 선택 | 근거 |
|---|---|---|
| 언어 | Python 3.14 + venv | 설치돼 있고 ficc-desk 코드를 재사용 |
| 웹 | FastAPI + uvicorn (포트 8790) | 타입 검증, ficc-desk와 8787 충돌 방지 |
| HTTP | httpx | 타임아웃·재시도를 명시적으로 다뤄 실패를 삼키지 않음 |
| DB | SQLite 표준 `sqlite3`, WAL, FTS5 | 단일 파일 백업, 뉴스·메모 검색(FTS5) |
| 시간대 | `tzdata` 패키지 + `zoneinfo` | 뉴욕 서머타임 자동 반영, Windows는 tzdata 필요. KST는 고정오프셋 |
| 프론트 | 빌드 없는 HTML/CSS/JS (ES modules) | Node 없음, 유지비 최소 |
| 차트(화면) | Apache ECharts 단일 파일, **버전 고정**, `static/vendor/`에 로컬 번들 | 드래그 줌·이중축·툴팁 내장, CDN 비의존·오프라인 |
| 폰트 | Pretendard 로컬 번들 (fallback 맑은 고딕), `tabular-nums` | 오프라인 |
| 텔레그램 | telethon (Phase 5, **읽기 전용**) | 사용자 계정 API로 구독 채널 접근 |
| RSS | `xml.etree` + httpx (표준 우선), 필요 시 `feedparser` 재검토 | 의존성 최소 |
| 테스트 | pytest | 파생값·내재수익률·파서·관측일·병합·리포트 |
| 리포트 차트 PNG | **matplotlib 3.11.2 + 맑은 고딕** (Phase 6 설치 2026-10-10, 사용자 승인) | 헤드리스(Agg), 화면과 동일 팔레트 hex 공유(`config/palette.py`). 대안: ECharts SSR(Node 필요 → 기각). 의존: numpy·pillow 등 9개(requirements.txt 고정) |
| 리포트 PDF | **HTML → Chrome 헤드리스 `--print-to-pdf`** (Phase 6 구현·실행 확인 2026-10-10) | Chrome이 이미 설치됨 → 추가 설치 0, 한글 폰트(맑은 고딕) 임베드 확인. 대안 weasyprint(Windows GTK 의존, 기각), pandoc+LaTeX(무거움, 기각) |
| 클라우드 수집 | GitHub Actions (비공개 저장소), ubuntu runner | 무료 한도 내 (§11) |
| 런처 | start.bat(ASCII) → start.ps1(UTF-8 BOM) → Chrome `--app` | ficc-desk 검증된 패턴 |

의존성을 추가할 때 이 표에 근거 한 줄을 남긴다. 사용하지 않는 것: Docker, ORM, 프론트 프레임워크/번들러, WebSocket, Drive API.

## 4. 폴더 구조

```
MarketView/
├── CLAUDE.md  SPEC.md  README.md(Phase 7)
├── .env  .env.example           (.env는 git 제외)
├── requirements.txt
├── config/                      sources.toml? → Python 모듈 marketview/config/*.py
├── data/marketview.db           (git 제외, 클라우드 동기화 폴더 밖)
├── data/cloud/                  (Actions가 커밋하는 날짜별 JSON — 클라우드 저장소에서만 커밋)
├── backups/  reports/  exports/
├── migrations/NNN_*.sql
├── marketview/
│   ├── app.py  db.py  derived.py  timeutil.py
│   ├── config/{settings,sources,series,news,events,telegram}.py
│   ├── collect/{base,ecos,fred,krx,yahoo,smbs,rss,telegram}.py   ← 로컬·Actions 공용
│   ├── ingest.py  backfill.py  cloudsync.py
│   ├── routes/  report/  static/ (vendor/, fonts/)  templates/
├── scripts/{start.bat,start.ps1,install_shortcut.ps1,discover_*.py,telegram_login.py,backup.py}
├── .github/workflows/collect.yml
└── tests/
```

## 5. 데이터 항목별 소스 매핑

지연: 게시 지연(관측일 대비). 모든 값은 `obs_date`(실제 관측일)로 저장하고 화면 툴팁에 출처·관측일을 표시한다.

### 5.1 확인됨 (ficc-desk sources.py, 확인 2026-08-11 — 주석에 확인일 유지, Phase 1 시작 시 재호출 재확인)

| series_key | 항목 | 소스 | 코드 | 단위 | 지연 |
|---|---|---|---|---|---|
| bok_base_rate | 기준금리 | ECOS | 722Y001 / 0101000 | % | T+1 |
| kofr | KOFR | ECOS | 817Y002 / 010901000 | % | T+1 |
| cd_91d | CD 91일 | ECOS | 817Y002 / 010502000 | % | T+0 |
| cp_91d | CP 91일 | ECOS | 817Y002 / 010503000 | % | T+0 |
| ktb_1y/3y/5y/10y/20y/30y | 국고채 | ECOS | 817Y002 / 010190000, 010200000, 010200001, 010210000, 010220000, 010230000 | % | T+0 |
| corp_aa3_3y | 회사채 3년 AA- | ECOS | 817Y002 / 010300000 | % | T+0 |
| corp_bbb3_3y | 회사채 3년 BBB- | ECOS | 817Y002 / 010320000 | % | T+0 |
| usdkrw | 원/달러(종가 15:30) | ECOS | 731Y003 / 0000003 | 원 | T+0 |
| sofr | SOFR | FRED | SOFR | % | T+1 |
| ust_3m/2y/5y/10y/30y | UST CMT | FRED | DGS3MO, DGS2, DGS5, DGS10, DGS30 | % | H.15: 4일 지연 관측 (08-11) |
| us_ig_oas / us_hy_oas | OAS | FRED | BAMLC0A0CM, BAMLH0A0HYM2 | % (0.78=78bp) | T+1 |
| eurusd / usdjpy | 환율 | FRED | DEXUSEU, DEXJPUS | | H.15: 4일 |
| vix | VIX | FRED | VIXCLS | 지수 | T+1 |
| ktbf_3y/10y/30y (+_oi) | 국채선물 종가·미결제 | KRX | `drv/fut_bydd_trd`, PROD_NM 정확일치 `3년국채 선물` 등, 정규장·스프레드 제외·최대거래량 | 가격/계약 | **T+1 이상** |

KRX 키 활성화 확인(2026-08-11). `bon/*` 채권 서비스는 401(활용신청 미승인) — 승인 시 국채전문유통시장 데이터 후보.

### 5.2 확인 필요 (Phase 1 탐색 스크립트로 실호출 후 확정)

| 항목 | 후보 / 계획 | 안 되면 |
|---|---|---|
| UST 커브 나머지 만기 | FRED DGS1MO, DGS6MO, DGS1, DGS3, DGS7, DGS20 | 해당 만기 "소스 없음" |
| WTI | FRED DCOILWTICO는 지연 8일로 제외했었음 → Yahoo `CL=F` 등 비공식 후보 | 수동 |
| KOSPI·KOSDAQ | KRX 지수 API `idx/kospi_dd_trd`·`idx/kosdaq_dd_trd` (2026-10-14 활용신청 승인·200 확인, `IDX_NM` 정확일치 `코스피`/`코스닥`의 `CLSPRC_IDX`) | Yahoo `^KS11`·`^KQ11` (KRX 게시 전 날짜 대체·장중. KRX 값을 야후가 덮지 않음) |
| S&P 500, NASDAQ | FRED SP500, NASDAQCOM 후보 (지연·라이선스 기간 제한 확인) | Yahoo `^GSPC` `^IXIC` |
| Russell 2000 | 후보 탐색 (Yahoo `^RUT` 등) | 수동 |
| DXY | FRED 없음. DTWEXBGS는 다른 지수라 DXY 라벨 금지. Yahoo `DX-Y.NYB` 후보 | 수동 |
| 장중 시세(지수·FX·WTI·미국채 선물·DXY) | Yahoo Finance 비공식 엔드포인트. **소스 모듈 단위로 격리** — 실패 시 해당 항목만 "갱신 실패" | 일별 소스로 대체 |
| 국내 크레딧(공사채 AAA, 은행채 AAA, 카드채 AA+, 여전채 AA-, 회사채 AAA) | 금융투자협회 채권정보센터 시가평가, 공공데이터포털. ECOS에는 회사채 AA-/BBB-·산금채 정도만 확인됨 | **CSV 붙여넣기 수동 입력 경로** |
| KRW IRS 커브 | 서울외국환중개 `http://www.smbs.biz/Exchange/IRS.jsp` (http만 가능) `IRS RATE` 표의 **Mid** 열. HTML 파싱 가능 여부 확인. 당일 값 게시 시각 확인 필요 | 수동 |
| 한국 CDS 5Y | worldgovernmentbonds는 JS 렌더링 → 단순 HTTP 불가. **수동 기본** | 수동 |
| NDF 1M | 무료 아웃라이트 소스 못 찾음. 제안: ① 수동 입력 유지, 또는 ② 현물+스왑포인트(smbs F/X Swap, 1M은 Bid/Offer만 있어 Mid 규칙 필요)로 **조회 시 계산하는 파생값**(저장 금지)으로 대체하고 "NDF"가 아니라 "선물환 1M(계산)"로 라벨. → §13 질문 | 비활성 |
| USD SOFR 스왑 커브 / 미국채 선물 커브 | 무료 소스 탐색 | 수동 또는 비활성(화면에는 "소스 없음 · 수동 입력" 유지) |
| 국채선물 내재수익률 | KRX 상품 명세로 표면금리 5%·6개월 이표 표준 가격식 **확인 후** 구현, pytest 검증 | 구현됨(`futures.py`). 표면금리 5%·6개월 이표 KRX 공식 명세 대조 완료(2026-10-14) |
| 국고채 입찰·금통위·FOMC 일정 | 한국은행·연준·기재부/KOFIA 공식 페이지에서 확인, `events` 설정에 출처 URL·확인일 기록. 날짜 추측 금지 | 수동 이벤트 |
| 미국 지표 발표일 | FRED `releases/dates` — release_id는 실호출로 확인 | 수동 |

원칙: 소스를 못 찾은 항목도 숨기지 않고 "소스 없음 · 수동 입력"으로 화면에 둔다. 소스 하나가 실패해도 나머지는 정상 진행.

## 6. 수집 구조 (로컬 + 클라우드)

```
                 ┌─────────── marketview/collect/* (한 벌) ───────────┐
 (A) 로컬 앱 ────┤ 시작 시 백필 · 장중 주기 · 새로고침 · 텔레그램       │
 (B) Actions ────┤ 매시간: 장중 스냅샷 · RSS · smbs · 일별 당일치        │
                 └────────────────────────────────────────────────────┘
 (B) → data/cloud/YYYY-MM-DD/{snapshots,news,pages}.json 커밋(비공개 저장소)
 로컬: 시작/새로고침 시 git pull → cloudsync.py가 SQLite에 병합(UNIQUE 키, 멱등)
```

**(A) 로컬 백필**: 소스가 과거를 보관하는 ECOS·FRED·KRX 일별치와 텔레그램은 앱 시작 시 `max(obs_date) 다음 영업일 ~ 오늘`을 소급 수집. 소급분은 `captured_at`이 관측일보다 뒤라서 구분된다. 앱이 켜진 동안 장중 주기(기본 15분, 설정) + 새로고침 버튼. 화면은 백필 완료를 기다리지 않고 즉시 뜬다. 최초 1회는 소스별 가능한 최대 기간(예: 3년)을 API로 백필.

**(B) 클라우드**: 대상은 나중에 다시 구할 수 없는 것만 — 장중 시간별 스냅샷, 뉴스 RSS, 당일 값만 보이는 페이지(smbs IRS 등), 일별 소스의 당일 수집(백필과 중복돼도 같은 결과). 텔레그램 세션·메시지는 **클라우드에 올리지 않는다**. 비공개 저장소, 키는 GitHub Secrets만, 로그·커밋에 키 노출 방지(마스킹, 요청 URL 로깅 금지 — FRED/ECOS는 URL에 키가 들어가므로 예외 메시지에서 URL 제거). 스케줄이 몇 분 밀리는 것은 정상. 실패한 회차는 Actions 로그와 앱 상단 바(`cloud_sync` 상태)에 표시.

**병합 멱등성**: 관측치는 `(series_key, obs_date, kind, obs_ts)` UNIQUE, 뉴스는 `link` UNIQUE. 같은 파일을 여러 번 병합해도 결과가 같다 (pytest).

## 7. DB 스키마 (`data/marketview.db`, SQLite WAL)

번호 마이그레이션 `migrations/NNN_*.sql` + `schema_version`. 시각은 ISO8601 + 오프셋(+09:00 또는 UTC `Z`는 쓰지 않고 KST로 통일). 아래는 초안이며 Phase 1에서 확정.

```sql
CREATE TABLE series_def (            -- 항목 사전: 순서·단위·소스는 코드가 아니라 데이터
  series_key TEXT PRIMARY KEY, label_ko TEXT NOT NULL, label_short TEXT,
  tab TEXT NOT NULL CHECK (tab IN ('bond','fx','credit','equity')),
  group_key TEXT,                    -- 'kr_short' | 'ktb' | 'ust' | 'curve:ktb' ...
  tenor_months INTEGER,              -- 커브 x축용
  unit TEXT NOT NULL,                -- pct | bp | krw | index | price | contracts
  decimals INTEGER NOT NULL, delta_unit TEXT NOT NULL,   -- bp | pct | abs
  provider TEXT,                     -- ecos|fred|krx|yahoo|smbs|manual|NULL
  source_ref TEXT, lag_note TEXT, status TEXT NOT NULL,  -- confirmed|unconfirmed|manual|inactive
  display_order INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE observation (           -- 미수집은 행이 없다. 0으로 채우지 않는다
  series_key TEXT NOT NULL REFERENCES series_def, obs_date TEXT NOT NULL,   -- YYYY-MM-DD
  kind TEXT NOT NULL CHECK (kind IN ('daily','intraday')),
  obs_ts TEXT NOT NULL DEFAULT '',   -- intraday는 관측 시각, daily는 ''
  value REAL NOT NULL, provider TEXT NOT NULL, origin TEXT NOT NULL CHECK (origin IN ('local','cloud','manual','import')),
  captured_at TEXT NOT NULL, note TEXT,
  PRIMARY KEY (series_key, obs_date, kind, obs_ts)) WITHOUT ROWID;
CREATE TABLE observation_log (       -- append-only: 덮어쓰기·수동 입력 이력
  id INTEGER PRIMARY KEY, series_key TEXT, obs_date TEXT, kind TEXT, obs_ts TEXT,
  action TEXT CHECK (action IN ('insert','update')), prev_value REAL, new_value REAL NOT NULL,
  prev_provider TEXT, new_provider TEXT, written_at TEXT NOT NULL);

CREATE TABLE ingest_run (id INTEGER PRIMARY KEY, origin TEXT, kind TEXT, started_at TEXT, finished_at TEXT,
  status TEXT CHECK (status IN ('running','ok','partial','failed')));
CREATE TABLE ingest_result (run_id INTEGER, source TEXT, series_key TEXT,
  status TEXT CHECK (status IN ('ok','miss','error','skipped')), message TEXT, PRIMARY KEY (run_id, source, series_key));
CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);  -- cloud 마지막 동기화 등

CREATE TABLE news (id INTEGER PRIMARY KEY, link TEXT NOT NULL UNIQUE, title TEXT NOT NULL, summary TEXT,
  source TEXT NOT NULL, published_at TEXT NOT NULL, tab TEXT, is_read INTEGER DEFAULT 0, fetched_at TEXT NOT NULL, origin TEXT);
  -- 본문 전체는 저장하지 않는다. + news_fts (FTS5: title, summary)
CREATE TABLE tg_message (id INTEGER PRIMARY KEY, channel TEXT NOT NULL, msg_id INTEGER NOT NULL, body TEXT,
  posted_at TEXT NOT NULL, links_json TEXT, attachments_json TEXT, tab TEXT, is_read INTEGER DEFAULT 0, fetched_at TEXT,
  UNIQUE (channel, msg_id));     -- + tg_fts
CREATE TABLE tg_message_edit (id INTEGER PRIMARY KEY, channel TEXT, msg_id INTEGER, body TEXT, edited_at TEXT);  -- 원본 보존, 수정본 별도

CREATE TABLE event (id INTEGER PRIMARY KEY, kind TEXT, title TEXT NOT NULL, starts_at TEXT NOT NULL, -- KST 변환 후 저장
  tz_source TEXT, region TEXT CHECK (region IN ('KR','US','OTHER')), importance INTEGER, consensus TEXT, memo TEXT,
  source_url TEXT, verified_on TEXT, origin TEXT, deleted INTEGER DEFAULT 0, updated_at TEXT);
CREATE TABLE event_log (...event 전체 컬럼 스냅샷..., logged_at TEXT);          -- 수정마다 이전 버전 append
CREATE TABLE view_note (id INTEGER PRIMARY KEY, note_date TEXT, tab_tag TEXT, body TEXT, updated_at TEXT);  -- + view_note_fts
CREATE TABLE view_note_log (... 이전 버전 append-only ...);
CREATE TABLE position (id INTEGER PRIMARY KEY, product TEXT, side TEXT CHECK (side IN ('long','short')), entry_level REAL,
  entry_date TEXT, target REAL, stop REAL, rationale TEXT, series_key TEXT, pnl_unit TEXT, status TEXT, updated_at TEXT);
CREATE TABLE position_log (... append-only ...);
CREATE TABLE app_setting (key TEXT PRIMARY KEY, value TEXT);  -- 색 규칙, 레이아웃 비율, 키워드, Drive 경로 등
CREATE TABLE report_run (id INTEGER PRIMARY KEY, report_date TEXT, version INTEGER, md_path TEXT, pdf_path TEXT, drive_status TEXT, created_at TEXT);
```

**파생값은 저장하지 않는다.** `derived.py` 레지스트리(문자열 eval 금지): CD−KOFR, 10−3, 30−10, 10−2, 신용 스프레드(회사채/공사채 등 − 국고 같은 만기), (선택) 선물환 1M. 구성 값이 하나라도 없으면 `None`, 화면은 "미수집". 금리 차이는 `(a−b)×100` bp. OAS는 원본 % 저장, 표시에서 bp.

**관측일 규칙**: 값마다 `obs_date`는 소스가 알려준 실제 관측일. 서로 다른 지연의 값이 한 타일 줄에 섞이므로 모든 타일·툴팁에 as-of 날짜 표시. 일별 종가(`daily`)와 장중 스냅샷(`intraday`)은 종류로 구분하고 같은 날 종가가 오면 daily로 별도 저장(스냅샷은 지우지 않음).

## 8. 화면 명세 요약

원문 §2~4를 그대로 따른다. 구현 시 확정할 항목만 적는다.

- 레이아웃: CSS grid, 좌 70 : 우 30, 우측 뉴스 60 : 이벤트 40, 하단 포지션 패널은 좌측 열 아래만(≈18%). 드래그 스플리터 + `app_setting` 저장. 1920×1080·1440×900 모두 `body{overflow:hidden}`, 스크롤은 패널 내부만.
- 메인 탭 [채권][외환][신용][주식] (키 1~4), R=새로고침. 타일 줄 → 클릭 시 아래 차트에 시리즈 추가(여러 개 겹침, 단위 다르면 우측 보조 축). 하위 뷰 [시계열][커브][데이터 표(CSV 내보내기)]. 기간 1M/3M/6M/1Y/3Y/전체. 커브: 오늘/1주/1개월 전(변경 가능) + 하단 bp 변화 막대.
- 채권 탭 그룹: 국내 단기, 국고채(+선물 가격·내재수익률), 국내 커브(국고·선물·KRW IRS), 미국, 미국 커브(UST·선물·SOFR 스왑). 기본 그래프: 국고 3·10Y + 10−3 / UST 2·10Y + 10−2.
- 과거 날짜 선택(as-of) 시 시세·뉴스·메모를 그날 기준으로 표시 (observation은 `obs_date<=D` 최신, 뉴스는 `published_at<=D`).
- 상단 바: 날짜·요일·KST/뉴욕 시각(초 단위, `Intl`/서버 zoneinfo), 마지막 갱신, 클라우드 마지막 동기화, 실패 소스 경고(클릭 시 상세), 리포트 생성 버튼, (선택) 한·미 장 상태.
- 뉴스: 중복 제거·최신순·접기·읽음 흐림·키워드 하이라이트·FTS 검색, 클릭 시 기본 브라우저. 텔레그램은 채널→기본 탭 + 키워드 보정(설정 파일).
- 이벤트: D-day, KR/US 텍스트 배지, 중요도, 오늘·내일 강조, 미국 시각 → KST 변환(서머타임·날짜 넘김, pytest), 수동 추가/수정(+컨센서스·메모, append-only).
- UI 규칙은 CLAUDE.md. 팔레트(화면·리포트 공용, 제안): 강조 `#2F5FB3`, 시리즈 `#2F5FB3 #E07B39 #3A9D8F #8E6BB5 #C2A12E #6B7785` (Phase 2에서 스크린샷으로 확정).

### API 개요 (FastAPI, `/api`)
`GET /series`, `GET /series/{key}?from&to&kind&asof`, `GET /tiles?tab&asof`, `GET /curve?family&dates`, `GET /table?keys&from&to(.csv)`, `PUT /observation`(수동), `POST /refresh`, `GET /status`(수집·클라우드·Drive), `GET/POST /news`, `GET /telegram`, `GET /search?q`, `GET/POST/PUT /events`, `GET/PUT /notes`, `GET/PUT /positions`, `POST /report?date`, `GET/PUT /settings`.

## 9. 일일 리포트 템플릿

`reports/YYYY/MM/YYYY-MM-DD.md` + `assets/*.png`, `reports/YYYY/MM/YYYY-MM-DD.pdf` (A4 세로, 흰 배경, 휴대폰 가독). DB가 원본, 리포트는 파생 결과물.

```
# MarketView 일일 리포트 — YYYY-MM-DD (요일)
생성: <KST 시각> · 데이터 기준: 국내 <전일 마감> / 미국 <전일 마감>

## 1. 요약          자산군별 표: 지표 | 레벨 | 전일 대비(금리 bp) | 관측일 | 출처   (미수집은 "미수집")
## 2. 차트          국고 커브(당일/1주/1개월), UST 커브, 주요 스프레드, 신용 스프레드, FX·주가지수 1개월
## 3. 뉴스          탭별: 제목 · 출처 · 시각 · 링크
## 4. 텔레그램 리서치  채널별 정리 (설정으로 on/off, 기본 on; 요약 기능 on이면 3줄 요약)
## 5. 다가오는 이벤트  향후 2주 (D-day, KST)
## 6. 포지션        그날 기준 포지션 현황·손익 (뷰 메모는 2026-10-10 에 제외)
## 7. 데이터 출처·관측일·수집 실패
```

규칙: 같은 날짜 재생성은 덮어쓰지 않고 `-v2`, `-v3`… (`report_run`에 기록). 노트북이 꺼졌던 날은 다음 기동 시 백필 후 날짜별로 생성. 실패해도 수집·화면은 계속. 로컬 → `G:\내 드라이브\MarketView\reports\YYYY\MM\` 복사(실패해도 로컬은 완료, 상단 바 표시). 옵시디언 복사는 선택·기본 꺼짐(삭제 금지, `.obsidian` 접근 금지).

## 10. 저장·보관·백업

- 전부 영구 보관. 일별 종가는 절대 삭제하지 않는다. 기사 본문은 저장 안 함(링크).
- 백업 (2026-10-09 결정): 원본 DB는 동기화 폴더 밖.
  - **일일**: 매일 `VACUUM INTO backups/marketview-YYYYMMDD.db` → gzip(`.db.gz`, 표준 `gzip`) → Drive `MarketView\backups\daily\`에 복사. **최근 30개 유지**, 초과분은 오래된 것부터 삭제(로컬 `backups/`도 동일).
  - **월말**: 매월 마지막 일일 백업을 `MarketView\backups\monthly\marketview-YYYYMM.db.gz`로 **영구 보관**(삭제 대상 아님).
  - **주간 CSV**: 시세·뉴스·메모를 `.csv.gz`로 압축해 `exports/` 및 Drive `MarketView\backups\csv\`에 **누적 보관**(DB가 깨져도 텍스트가 남게 함).
  - 리포트(MD·PDF·PNG)는 압축하지 않고 Drive `reports\`에 영구 누적.
  - 롤링 삭제는 `daily\` 폴더의 `marketview-YYYYMMDD.db.gz` 패턴 파일에만 적용한다. 그 외 파일은 건드리지 않는다.
- README에 복원 절차(`.gz` 해제 → DB 교체)를 쓰고 Phase 7에서 실제 복원 테스트.

### 용량 추정 (가정 명시, Phase 1에서 실측으로 교정)

| 항목 | 계산 | 1년 |
|---|---|---|
| 일별 시계열 | ~70계열 × 252영업일 × ~100B | ≈ 2 MB |
| 장중 스냅샷 | ~25계열 × 하루 ~16회 × 252일 = 약 10만 행 × ~100B | ≈ 10 MB |
| 뉴스 | ~300건/일 × 365 × ~0.7KB(+FTS) | ≈ 100 MB |
| 텔레그램 | ~200건/일 × 365 × ~1.5KB(+FTS) | ≈ 150 MB |
| 메모·이벤트·포지션·로그 | 소량 | < 5 MB |
| **합계** | | **≈ 250~300 MB/년** |

백업 용량(추정, 텍스트 압축률 3~8배 가정 → 실측 후 교정): 압축 전 최악 30개 × ~300MB ≈ 9GB, gzip 후 DB 한 벌이 연 50~100MB 수준이면 일일 30개 ≈ 1.5~3GB(1년 차 기준, DB가 커질수록 증가), 월말 영구본은 연 12개 ≈ 0.3~1GB씩 누적. Drive 무료 15GB(다른 파일과 공유) 안에서 수년 운용 가능하나 Phase 7에서 실측 후 재계산한다. **제안**: 장중 스냅샷은 90일 이후 일별 1건(마감 직전 값)으로 압축하되 일별 종가 테이블은 건드리지 않음, 압축 전 원본은 월별 CSV로 보관.

## 11. GitHub Actions 사용량

- **확인 2026-10-10 (공식 문서 docs.github.com)**: GitHub Free 개인 계정 비공개 저장소 월 **2,000분**(매월 초 초기화), 아티팩트 저장소 500MB, 공개 저장소는 표준 러너 무료. 문서가 분 배율 대신 분당 금액(Linux 2코어 $0.006, Windows $0.010, macOS $0.062)만 안내해서 **Linux 1× 가정은 첫 실행 후 Settings > Billing 으로 실측 확인**. 스케줄: UTC, 최소 5분 간격, **기본 브랜치에서만** 실행, 정각에 지연 집중(→ 17·47·37분 사용), 부하가 크면 큐의 회차가 버려질 수 있음. 60일 무활동 자동 비활성화는 공개 저장소 문서에만 있고 비공개는 언급 없음(데이터 커밋이 계속 활동을 만든다).
- 시간당 1회 × 24 × 30 = 720회. 회당 1~2분(각 실행은 분 단위 올림) → **720~1,440분/월**. 한도 안이지만 여유가 크지 않음.
- **실측(2026-10-15, 작업별 올림 추정)**: 10-09~10-14 합계 57분, 스케줄만 도는 날 하루 6~8분 → 월 약 200~250분(위 예상의 1/3~1/5; 평일 스케줄이 예상보다 적게 돌고 회차가 짧다). 공식 값은 Settings > Billing.
- 제안: 시장이 열린 시간대(KST 07:00~익일 07:00 중 필요한 구간)로 cron 제한하거나, 소스별 주기를 분리(뉴스 1시간, 일별 소스는 하루 2~3회). 목표 회당 ≤1분, 월 ≤ 1,000분.
- cron은 UTC 기준, 몇 분 지연 정상. 데이터 폴더 커밋으로 저장소가 커지므로 날짜별 JSON은 압축/월별 병합 검토.

## 12. 구현 단계 (원문 §7)

| Phase | 내용 | 완료 기준 (실행 증거) |
|---|---|---|
| 0 | SPEC·CLAUDE | 승인 |
| 1 | 탐색 스크립트로 확인 필요 실호출 → 소스 확정, DB·수집 모듈, 백필, 시작 시 자동 백필 | 탐색 출력, pytest, 백필 후 행 수·구간 |
| 2 | 레이아웃·탭·더미 데이터 | 1920×1080·1440×900 스크린샷, 사용자 확인 |
| 3 | 메인 패널 실데이터 + 시계열/커브/표 | 스크린샷 |
| 4 | 이벤트, 뷰·포지션 | 동작 스크린샷, 로그 테이블 |
| 5 | 뉴스 RSS → 텔레그램, 저장·검색 | 수집 결과, 중복 방지 테스트 |
| 6 | 일일 리포트 MD+PDF, Drive 복사 | 실제 PDF 한글 확인 |
| 7 | 런처, 백업, CSV, 복원 테스트 | 복원 실행 로그 |
| 8 | GitHub Actions, Secrets, 로컬 병합 | Actions 실행 로그, 병합 pytest |

각 Phase 끝: git 커밋 · 실행 증거 · 확인 필요 목록 갱신.

## 13. 결정 사항 (2026-10-09 사용자 답변 반영)

1. **불편했던 점**: 사용자가 설계에 이미 반영했다고 함 → 별도 기재 없음.
2. **Drive 경로 확정**: `G:\내 드라이브\MarketView\` 폴더를 만들고 그 아래 `reports\`, `backups\`로 정리 (2026-10-09 생성 완료). 설정 파일의 Drive 경로는 `G:\내 드라이브\MarketView`.
3. **NDF 1M**: **수동 입력 유지** (파생 대체 안 함).
4. **PDF·차트 도구**: **승인** — 차트 PNG는 matplotlib, PDF는 Chrome 헤드리스 print-to-PDF.
5. **옵시디언 복사**: **보류** (나중에 결정, 기본 꺼짐, 지금은 구현하지 않음).
6. **상승/하락 색**: **한국식 확정** (상승 빨강 / 하락 파랑), 설정에서 전환 가능.
7. **백업 보관**: **일일 30개 + 월말 영구 보관**, gzip 압축 (§10). 확정.
8. **설치 승인(미결)**: Phase 8의 `winget install --id GitHub.cli`는 설치 직전에 명령어를 다시 보여주고 개별 승인을 받는다. matplotlib·tzdata·telethon 등 venv 패키지는 Phase 1 시작 시 `pip install` 명령을 보여주고 승인받는다.

9. **화면 구성 변경 (2026-10-10 사용자 결정, 원문 요구사항보다 우선)**: ① **KRW IRS 는 1·3·5·10년만** 화면·커브·리포트에 표시(소스가 한 페이지로 모든 만기를 주므로 수집·저장은 전 만기 유지, `config/layout.py` 의 `IRS_TENORS`). ② **USD SOFR 스왑 제외**(항목 정의·타일·커브 삭제). ③ **신용 탭에서 섹터 수익률·섹터 스프레드 제외**(공사채·은행채·카드채·여전채·회사채 AAA 항목, 파생 스프레드, 만기 선택 UI 삭제) — 회사채 AA-/BBB- 3년(ECOS)과 그 스프레드만 남김. ④ **뷰 작성 기능 제외** — 새 창(/note)·`/api/notes`·리포트의 메모 절·CSV 의 view_note 삭제, **포지션만 유지**. `view_note`/`view_note_log` 테이블은 append-only 이력이라 지우지 않고 남겨 둠(기존 행 0건). ⑤ **지표 검색 추가**: 메인 패널 오른쪽 위 **돋보기 아이콘**(클릭 또는 `/` 키로 입력창이 펼쳐지고, 입력이 비면 Esc·바깥 클릭·포커스 이동으로 아이콘으로 접힘) → `/api/find?q`. **화면 타일에 나오는 지표뿐 아니라 저장돼 있는 모든 정의된 지표**(국고 1·20년, IRS 2·4·7·12·15·20년, UST 1·3·6개월·1·3·7·20년 등 15개 + 파생값)를 라벨·키·그룹·탭·출처 이름(예: `fred`, `파생`)으로 찾는다(낱말 AND, `10-3` 처럼 하이픈 입력도 `10−3` 에 맞음, 순위: 라벨 일치 → 화면에 나오는 것 → 정의 순서). 값·관측일·출처를 같이 보여 주고 화면에 없는 것은 "화면 타일에 없음"으로 표시. 선택하면 해당 탭으로 이동해 차트에 올리고(타일이 있으면 그 그룹으로도), 화면에 없는 지표는 차트로만 열며 선택은 새로고침 후에도 유지된다(`/api/meta.known_keys` 로 복원). 정의에서 뺀 USD 스왑·신용 섹터는 정의·데이터가 없어 검색되지 않는다. 기준일(as-of)을 고르면 그날 종가로 보여 준다. ⑥ 뉴스 3줄 요약은 **보류**.
10. **마감 기준 단일 모드 (2026-10-14 사용자 결정)**: 지표마다 게시 시점이 달라(장중 Yahoo=오늘, ECOS·KRX=전일, FRED=1~3일 전) 화면의 날짜가 제각각이던 문제를 줄이려고 **화면·포지션 현재값을 항상 권역별 마감일의 확정값으로 통일**한다(장중 모드는 만들었다가 의미가 적어 같은 날 없앴다). 실시간 소스(증권사 API)도 쓰지 않는다. 권역 마감일 = `queries.common_close_dates`(한국=ECOS·KRX·smbs, 미국=FRED·Yahoo 각각 화면 지표 60% 이상이 일별 값을 가진 가장 최근 날, 못 채우면 값이 있는 가장 최근 날). 한국·미국을 한 날짜로 맞추지 않는 이유: 미국이 늦게 게시되어 맞추면 한국 최신 값을 버리게 된다. 권역 마감일보다 오래된 타일은 날짜를 굵게·점선으로 표시한다(기준금리처럼 결정 때만 바뀌는 `step` 지표는 제외). 기준일을 직접 고르면 그 날짜가 우선. 상단 표시: 마감 기준 한국 MM-DD · 미국 MM-DD. API `mode=close|live` 는 남아 있으나 기본·화면은 close. 장중 스냅샷(Yahoo) 수집은 계속 하지만 화면·리포트에서 쓰지 않는다.

### 사용자가 직접 해야 하는 단계 (해당 Phase에서 순서대로 안내)
- Telegram: my.telegram.org에서 api_id/api_hash 발급 → `.env` 기입 → 터미널에서 로그인 스크립트 실행(전화번호 인증코드) (Phase 5)
- GitHub: 계정 로그인(`gh auth login`), 비공개 저장소 생성 승인, Secrets(ECOS/FRED/KRX 키) 등록 (Phase 8)
- Google Drive 데스크톱 앱 설치·로그인 (이미 `G:` 있음 — 확인만)

## 14. Phase 1 결과 (2026-10-09) 와 남은 확인 필요

### 확인됨 (실호출, 코드는 `marketview/config/sources.py`)
| 항목 | 결과 |
|---|---|
| UST 1M·6M·1Y·3Y·7Y·20Y | FRED DGS1MO/DGS6MO/DGS1/DGS3/DGS7/DGS20 전부 응답. 지연 ~2일 (10-08 15:16 갱신 시 최신 관측일 10-07) |
| S&P 500, NASDAQ | FRED SP500, NASDAQCOM. 지연 ~1일. SP500은 FRED 라이선스상 최근 10년만 제공 |
| WTI | FRED DCOILWTICO 현물(지연 ~3일, 08-11에는 8일이었음) + Yahoo CL=F 근월선물(장중). 두 계열 `wti`, `wti_fut`로 분리 (선물은 롤오버 때 레벨이 튄다) |
| Russell 2000 | FRED `RU2000PR` HTTP 400(없음) → Yahoo `^RUT` |
| DXY | FRED 없음(DTWEXBGS는 다른 지수, 채택 안 함) → Yahoo `DX-Y.NYB` |
| KOSPI·KOSDAQ | ECOS 일별 주가지수 통계표 없음(월별뿐). KRX `idx/kospi_dd_trd`·`kosdaq_dd_trd` 는 2026-10-14 활용신청 승인 후 200, 종가는 Yahoo 와 일치 확인. `idx/krx_dd_trd` 는 미신청(401) |
| 미국채선물 | Yahoo ZT/ZF/ZN/ZB=F (가격, 근월 연속선물) |
| Yahoo | 14개 심볼 전부 200. 일별(3년)·장중 스냅샷 수집 동작. 비공식이라 항목 단위로 격리 |
| KRW IRS | smbs 값이 `%_Xhh` 16진 문자코드로 인코딩 → 디코딩 파서 구현, 열 순서 기일물/Receive/Mid/Pay 확인, **과거 날짜 조회 가능**(폼 필드 12개 필요) → 1년(246영업일) 백필 완료. 기일물 1·2·3·4·5·7·10·12·15·20Y |
| 국채선물 명세 | 표면금리 연 5%, 6개월 이표, 3·5·10·30년 표준물 — 증권사 상품명세·언론으로 교차 확인. **2026-10-14 KRX Global 공식 명세(3·10·30년)로 대조 완료: 표면금리 5%·6개월 이표·현금결제 일치. 5년물은 수집·표시 대상이 아니라 미대조** |
| 내재수익률 | 표준물 반년복리 이론가로 계산(`futures.py`). 실제 국고채 대비 3Y +12bp, 10Y +5bp, 30Y +3bp 안팎으로 같은 방향 → 레벨 비교용 참고치 (CTD 기준 아님) |
| ECOS·FRED·KRX 기존 코드 | ficc-desk 확인(2026-08-11) 코드를 10-09 실호출로 재확인 (ECOS 13, KRX 5계열) |

### 백필 결과 (data/marketview.db)
56개 계열 · 34,686행 · 8.3MB · 중복 키 0 · 값 0 행 0. ECOS·FRED·Yahoo는 3년(2023-10~), KRX·smbs는 1년.
용량 환산: 일별 56계열 3년 ≈ 8MB(위 §10 추정과 같은 자릿수).

### Phase 3 결과 (2026-10-09)
- API: `/api/meta · tiles · series · curve · observation(PUT, 수동 항목만) · status · refresh` (`marketview/api.py`, 계산은 `queries.py`). 화면 구성(탭·그룹·타일·커브 패밀리)은 `config/layout.py`.
- 타일 값 규칙: 일별 종가(공식)가 기본. 새 거래일의 장중 값이 있으면 장중 값을 보여주고 전일 대비는 마지막 공식 종가 기준. 과거 기준일을 고르면 일별 값만 쓴다. 파생값은 구성 값의 **관측일이 같은 날짜끼리만** 계산한다(없으면 "구성 값 미수집").
- OAS는 원본 %를 저장하고 표시만 bp로 환산(×100). 금리 변화는 bp, 지수·WTI는 %, 환율·선물가격은 절대값.
- 커브: 국고채 · 국채선물 내재 · KRW IRS · UST · 미국채선물(가격) · USD 스왑(수동, 값 없으면 안내). 비교일에 값이 없으면 7일 look-back 안에서 직전 관측을 쓰고 툴팁에 실제 관측일을 표시한다.
- 신용 섹터는 만기 1·3·5년 선택(국고채와 같은 만기로 스프레드 계산). 소스가 없는 항목은 타일 클릭 → 입력 폼(기본 날짜 = 국고채 최신 관측일, 이력 남김, 자동 수집이 덮어쓰지 않음).
- 앱 시작 시 백그라운드 백필·스냅샷, 30초마다 수집 완료를 감지해 화면을 조용히 갱신. 새로고침 버튼은 수집을 돌리고 끝나면 다시 그린다(약 20~30초).
- 아직 더미: 뉴스(Phase 5), 이벤트(Phase 4), 포지션 표(Phase 4). 패널 제목 옆에 "더미" 표시.
- 화면 상태를 주소로 지정할 수 있다: `/?tab=bond&group=ktb&sub=curve&family=ktb&range=3M&keys=a,b&asof=YYYY-MM-DD` (저장 안 함, 스크린샷·런처용).

### Phase 4 결과 (2026-10-09)
**이벤트** (`events.py`, `config/events.py`, 테이블 `event`/`event_log`)
| 이벤트 | 출처 (확인 2026-10-09) | 시각 |
|---|---|---|
| FOMC 금리 결정 (2026·2027) | 연준 https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm — 2026: 1/28 3/18 4/29 6/17 7/29 9/16 10/28 12/9 (회의 둘째 날). 2027은 페이지가 "잠정(tentative)" | 14:00 ET — 연준 보도자료 "For release at 2:00 p.m. EDT" |
| 금통위 통화정책방향 | 한국은행 공식 표 https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755 — 2026: 1/15 2/26 4/10 5/28 7/16 8/27 **10/22** 11/26 | 공식 표에 시각 없음 → **"시각 미정"** (추측하지 않음) |
| 미국 CPI·고용·PCE·GDP | FRED `release/dates` (release_id 10·50·54·53 는 `fred/releases` 목록의 이름 일치로 확인). 발표일이 BLS·BEA 일정과 일치함을 확인(CPI 10/14·11/10, 고용 11/6, GDP·PCE 10/29) | 08:30 ET — BLS CPI·고용 일정 "08:30 AM", BEA 일정 "8:30 a.m." |
- 현지 날짜·시각·시간대를 저장하고 **조회 때** `zoneinfo`로 KST를 계산한다(서머타임·날짜 넘김 자동: 10/28 14:00 EDT → 10/29 03:00 KST, 11/6 08:30 EST → 22:30 KST).
- 동기화(앱 시작 + 새로고침 때): 출처가 주는 칸만 갱신하고 사용자가 쓴 컨센서스·메모·중요도는 보존. 출처에서 사라진 미래 항목은 `stale`, 되돌아오면 복구. 사용자가 지운 항목은 되살리지 않음. 한 릴리스가 실패해도 나머지는 계속(상단 경고에 표시).
- 공식·FRED 항목은 날짜·시각·제목 잠금(컨센서스·메모·중요도만 수정), 수동 항목은 전체 수정. 모든 변경은 `event_log`에 변경 후 전체 상태로 남는다.
- **자동 소스를 못 찾아 수동 추가**: 국고채 입찰일(재정경제부 월별 발행계획 첨부 PDF `R26100075.pdf` 가 이미지 압축이라 자동 판독 실패, 2026-10-09 — 10월 발행 물량 12.0조(3년 2.4·10년 2.1·30년 1.8조)만 확인), 한국 CPI(통계청), BOJ·ECB.

**나의 뷰** (`view_note`/`view_note_log`): 날짜+자산군 태그별 메모를 새 창(/note)에서 작성. 입력 후 1.2초에 자동 저장하고, 저장할 때마다 변경 후 본문이 로그에 쌓인다(같은 내용은 새 버전을 만들지 않음). 지난 메모 목록·검색은 부분일치(LIKE) — 한국어는 공백 토크나이저(FTS5)로 단어 중간 검색이 안 되고 메모가 하루 4건 이하라서. (뉴스·텔레그램 검색은 Phase 5에서 FTS5 trigram 재검토)

**포지션** (`position`/`position_log`): 상품, 연결 시세(선택), 방향, 진입 레벨·진입일, 목표, 손절, 근거, 청산(레벨+일). **방향**: 금리(% 단위: 국고·UST·IRS·회사채 금리·국채선물 내재수익률 등)는 **채권 관행** — 롱 = 채권 롱 = 금리 하락 시 이익, 숏 = 금리 상승 시 이익. 스프레드(bp)·가격·지수·환율은 **레벨 기준** — 롱 = 레벨 상승 시 이익(2026-10-15 사용자 결정, 이전에는 모두 레벨 기준이었다. `migrations/002` 주석의 '레벨 기준'은 옛 설명이며 저장값 long/short 는 그대로다). 목표·손절 방향 경고도 같은 규칙을 따른다. 손익: 금리 % → bp, 스프레드 bp, 원화 환율 원, 지수·가격 pt. 시세가 연결되지 않았거나 값이 없으면 현재값·손익을 "—"/미수집으로 비운다. 청산분은 확정 손익, 기본 목록에서는 숨기고 "청산 포함"으로 본다.

**수동 시세 CSV**: 소스 없는 항목(신용 섹터 등)을 `날짜,항목키,값` 또는 `date,키1,키2…` 표로 붙여넣어 저장. 한 줄이라도 틀리면 전부 취소, 빈 칸은 미수집으로 건너뜀(0 으로 채우지 않음).

### Phase 5 결과 — 뉴스 (2026-10-10)
**RSS 소스 확인** (`scripts/discover_rss.py` 실호출 2026-10-09 → `config/news.py`에 고정): 채택 24개 — 구글 뉴스 검색 쿼리 8개(탭별 한국어 4 + 영어 4, `when:2d`), 한국경제 2, 매일경제 2, 연합뉴스 2, 연합인포맥스, 이데일리, 머니투데이, CNBC 2, MarketWatch, Investing.com, 연준·ECB·한국은행 보도자료.
**제외**: 서울경제(404), Yahoo Finance(404), MarketWatch marketpulse(최신이 1년 전), CNBC bonds(finance 와 같은 피드).
**피드마다 다른 형식(실측)과 처리**: RFC822 `+09:00`(콜론, 매일경제) · 시간대 없는 날짜(인포맥스=KST, Investing=UTC, 피드 설정 `naive_tz`) · `9 Oct`(0 패딩 없음) · 이중 HTML 이스케이프 제목(머니투데이) · 구글 뉴스는 링크가 리다이렉트 URL·제목 끝 ` - 언론사`·요약이 관련 기사 목록(요약 저장 안 함). 날짜를 못 읽으면 항목을 버린다(현재 시각으로 채우지 않음), 미래 시각은 현재로 눌러 둔다.
**저장**: 제목·요약(400자)·출처·링크·발행시각만 (본문 저장 안 함). 링크(추적 파라미터 제거)가 UNIQUE → 같은 입력을 몇 번 넣어도 중복 없음. 같은 기사가 여러 피드로 오면(`title_key`) 저장은 따로, 화면은 먼저 저장된 것 하나만. DB 에는 영구 보관, 화면 기본 조회는 14일, 검색은 전체 기간.
**분류**: 키워드 점수(제목 3·요약 1·피드 기본 탭 +2, 동점 시 신용>외환>채권>주식). 기본 탭이 없는 일반 피드는 **제목 키워드가 있어야** 분류(요약에 '달러'가 한 번 나왔다고 외환 기사가 되지 않음). '위안'(위안부), '크레딧'(연예) 같은 오분류를 실측으로 찾아 키워드를 구체화함. 분류 안 된 기사는 탭엔 안 나오고 검색엔 나온다.
**검색**: FTS5 `trigram`(단어 중간 부분일치, 3글자 이상) + 2글자 이하는 LIKE. 따옴표·`%`·`&` 등 특수문자가 쿼리를 깨지 않는다(테스트).
**읽음**: 서버에 저장(`is_read`), 제목 링크는 기본 동작(새 창)을 그대로 두고 읽음만 표시. 키워드 하이라이트는 `config/news.py` 의 `HIGHLIGHT_KEYWORDS`. 기준일(과거 날짜)을 고르면 그날 23:59 KST 까지의 뉴스만 보인다.
**수집 주기**: 앱 시작 · 새로고침 · 15분마다. 피드는 병렬(8)로 받아 약 2~3초(24개), 한 피드가 실패해도 나머지는 계속(상단 경고).

**텔레그램 (Telethon, 읽기 전용)** — 코드·테스트는 끝났고 **실제 연결은 사용자 로그인이 필요**하다.
- 구현: `collect/telegram.py`(채널별 마지막 `msg_id` 이후만 수집, (채널, msg_id) 중복 방지, 최근 72시간 메시지는 다시 읽어 **수정본을 `tg_message_edit` 에 따로 기록 — 원본 유지**), `scripts/telegram_login.py`(로그인 + 구독 채널 key 목록 출력), `config/telegram.py`(CHANNELS: 채널 key·이름·기본 탭, 키워드 규칙으로 보정).
- 읽기 전용 보장: 전송·삭제·편집·읽음 처리 호출이 코드에 없음을 테스트가 검사. 세션(`*.session`)·`.env` 는 `.gitignore`. 클라우드에는 올리지 않는다.
- 설치·로그인 전에는 앱이 정상 동작하고 텔레그램만 "미연결"(클릭하면 순서 안내). `telethon` 은 아직 설치하지 않았다(설치 승인 필요).
- 첨부 PDF: 지금은 파일명 칩을 누르면 텔레그램의 해당 메시지(t.me 링크)가 열린다. **PDF 를 직접 내려받아 여는 기능은 로그인 후 실제로 시험할 수 있을 때 추가**(미검증 코드를 넣지 않음).
- (선택) ANTHROPIC_API_KEY 하루치 3줄 요약: 기본 꺼짐, 아직 구현하지 않음.

### Phase 6 결과 — 일일 리포트 (2026-10-10)
- 코드: `marketview/report/{data,charts,render,pdf,run}.py`, `api_report.py`, 마이그레이션 `004_report_run.sql`, CLI `python -m marketview.report [YYYY-MM-DD]`. 화면은 상단 "리포트 생성" 패널(기준일·텔레그램 포함 on/off·생성·결과 링크)과 Drive 복사 실패 시 상단 바 경고 버튼.
- 구성: §9 의 7개 절. 시세는 **기준일까지의 일별 종가만** 사용(장중 값 제외). 값이 없으면 "미수집", 파생값은 구성 값이 하나라도 없으면 "미수집". 소스 없는 수동 항목은 요약표에서 빼지 않고 7절 "수동 입력 대기"에 이름을 모두 나열.
- 차트 6개(PNG, 150dpi): 국고 커브·UST 커브(당일/1주/1개월), 주요 스프레드 3개월, 신용 스프레드 3개월, FX·주가지수 1개월(시작일=100). 팔레트·출처·기준일을 그림에 표기. 차트 하나가 실패하거나 데이터가 없어도 나머지는 만들고 사유를 7절에 남김.
- 파일: `reports/YYYY/MM/YYYY-MM-DD[-vN].md|pdf` + `assets/<stem>_<이름>.png`. 같은 날짜 재생성은 `-v2`… (DB 행뿐 아니라 **디스크에 파일이 있어도** 덮어쓰지 않음). `report_run` 에 md/pdf 경로·PDF 상태·Drive 상태·실패 사유 기록.
- 실패 격리: PDF 실패 → Markdown 유지, Drive 폴더 없음/복사 실패 → 로컬 완료 + `drive_status=failed`(없는 Drive 폴더를 만들어내지 않음). pytest 로 검증.
- 실행 증거(2026-10-10, 실제 DB 의 **복사본** + 임시 reports/Drive 폴더): 생성 8~14초, PDF 1.2MB, 한글 폰트 `MalgunGothic` 임베드 확인, v1→v2 버전 분리, 임시 Drive 폴더로 md·pdf·assets 복사. 캡처: `docs/screenshots/phase6_*.png` (PDF 원본 HTML 을 Chrome 으로 렌더한 것 + 차트 PNG).
- 설정: Drive 경로 기본 `G:\내 드라이브\MarketView`(환경변수 `MARKETVIEW_DRIVE_ROOT` 로 변경), reports 위치 `MARKETVIEW_REPORTS_DIR`(시험용). 텔레그램 포함 여부는 `app_setting.report_telegram`(기본 on).

### Phase 7 결과 — 런처·백업·CSV·복원 (2026-10-10)
- 런처: `scripts/start.bat`(ASCII) → `start.ps1`(UTF-8 BOM) → 서버(8790) 확인·기동 → Chrome `--app`. `install_shortcut.ps1` 이 바탕화면과 저장소 루트에 `MarketView.lnk` 생성(아이콘 `make_icon.py` 가 표준 라이브러리로 생성). 실행 증거: 아이콘 실행 6초에 HTTP 200·Chrome 창 "MarketView", 두 번째 실행에서 리슨 포트 1개 유지.
- 백업 `marketview/backup.py` (CLI `python -m marketview.backup daily|csv|all|restore`): `VACUUM INTO` 스냅샷 → 무결성 검사 → gzip. 일일 30개·월별(그 달 마지막 일일 백업, 달이 바뀌면 고정)·주간 CSV.gz 누적. 롤링 삭제는 `daily/marketview-YYYYMMDD.db.gz` 패턴에만. 앱 워커가 시작 시·15분마다 "오늘 백업 없음 / CSV 7일 경과 / Drive 실패 재시도"를 확인한다. 결과는 `sync_state`(`backup_daily`,`backup_csv`)와 `/api/status.backup`, 상단 바 "Drive 복사 실패" 버튼.
- **텔레그램 제외 결정**: DB 에 텔레그램 메시지 1,000건이 있어 규칙 6(메시지는 클라우드에 올리지 않음)에 따라 **Drive 로 가는 DB 사본은 텔레그램 테이블을 비우고 FTS 인덱스 재구성 + VACUUM** 한다(테스트가 gunzip 한 바이트열에 본문이 없음을 검사). CSV 에는 텔레그램을 넣지 않는다. 로컬 백업에는 전부 있다. 바꾸려면 사용자 결정.
- 복원 시험(실제 DB): 백업 → 복원 → 18개 표 행 수 비교 일치(`sync_state` 만 스냅샷 뒤 기록분). 복원본 `observation` 34,777·`news` 1,452·`tg_message` 1,000, Drive 사본 `tg_message` 0. 크기: 로컬 DB 5.5MB(gz)·Drive 사본 2.2MB(gz)·CSV 합계 0.44MB. `restore` 는 검사 통과 후에만 기존 DB 를 `.before-restore-<시각>` 으로 옆에 보존하고 교체(손상 파일은 거부, 기존 파일 유지 — 테스트).
- 용량 재계산(§10 가정 교정): 현재 DB 17MB(원본)·일일 gz 5.5MB(로컬)/2.2MB(Drive). 30개 롤링 ≈ 로컬 165MB·Drive 66MB, DB 가 연 250~300MB 로 커지면 gz 약 1/3 → 일일 30개 ≈ 3GB 안팎(Drive 15GB 안에서 수년 운용 가능, 월별 영구본 연 12개 누적은 1~3GB/년 수준으로 늘어남 — 1년 뒤 실측 재확인).
- 꺼져 있던 날의 리포트 자동 생성은 아래 "리포트 자동 생성" 에서 구현.

### Phase 8 결과 — 클라우드 수집기 (2026-10-10, 완료)
- **구조**: `.github/workflows/collect.yml` → `python -m marketview.cloud collect --mode snapshot|daily|news` (로컬과 같은 `collect/` 한 벌, 의존성은 `requirements-cloud.txt` 7개 — FastAPI·matplotlib 불필요, 깨끗한 venv 에서 임포트·실행 확인). 러너는 빈 임시 SQLite 로 수집(일별 소스는 `recent_days=10` 이라 3년 백필 안 함) → 이번 회차 행만 `data/cloud/YYYY-MM-DD/{snapshots,pages,news}.json` 에 키별로 합쳐 쓰고(같은 회차를 두 번 돌려도 결과 동일) `data/cloud/status.json` 을 남긴다. 전부 실패한 회차만 Actions 를 실패로 표시.
- **SPEC 변경**: 데이터는 코드 브랜치가 아니라 **`cloud-data` 브랜치**에 커밋한다(코드 히스토리 분리, 로컬은 이 브랜치만 fetch).
- **로컬 병합** `marketview/cloudsync.py`: `git fetch origin cloud-data` 후 `git ls-tree`/`git show` 로 **읽기만** 한다 — 작업 트리·현재 브랜치·미커밋 변경을 건드리지 않아 충돌이 없다(테스트로 검증). blob 해시로 이미 합친 파일은 건너뜀, `db.upsert_observation` 규칙(수동 값 보호, 같은 값 무변경)·뉴스 `INSERT OR IGNORE`. 앱 수집 사이클(시작·15분마다·새로고침)마다 실행. 원격 없음/브랜치 없음/네트워크 오류는 상태로만 남고 수집·화면은 계속. 상단 "클라우드 동기화"는 마지막 클라우드 회차 시각(원격 status.json), 클라우드 회차의 실패는 "수집 경고"에 `cloud:<소스>` 로 합쳐 표시.
- **스케줄(UTC)**: 평일 `17 0-7,13-21`(KST 09~16·22~06시 = 한국장·미국장) snapshot, 매일 `47 23,8,13`(KST 08:47·17:47·22:47) daily, 주말 `37 */4` news. 예상 월 ~520회 × 1~2분 ≈ 520~1,040분(한도 2,000). **실측으로 교정 필요**.
- 실측(로컬, 한국 IP): daily 26초(94개 호출 전부 성공), 산출 JSON 합계 ≈1MB(뉴스 0.97MB 가 대부분 — 피드가 한 번에 주는 ~1,400건). **저장소 증가 주의**: 뉴스 파일은 하루 내 회차마다 갱신(깃이 델타 압축), 연 수십~수백MB 가능 → 월별 병합·압축(SPEC §11)은 1~2개월 실측 후 결정.
- **첫 실제 실행 (2026-10-10, run 37957710840, 비공개 수집 저장소)**: 성공. 러너에서 Python 3.14·`git worktree add --orphan`·`cloud-data` 브랜치 첫 생성·푸시 모두 동작. **미국 IP 에서 ECOS·FRED·KRX·smbs·Yahoo 전부 접근 가능**(94개 호출 중 93 ok), 실패 1건은 머니투데이 RSS 타임아웃(`mt`) — 회차는 `partial` 로 기록되고 나머지는 계속됨. 산출 pages 341 · snapshots 14 · news 1,289 (JSON 합 0.9MB). 소요: 잡 1분 17초(→ 분 단위 올림 2분 과금 추정). 로컬 병합(복사본 DB): 1차 관측치 13·뉴스 23 합침, 2차 0(멱등), 작업 트리 변화 없음. 앱 재시작 후 `/api/status.cloud` = ok·daily·partial, "수집 경고"에 `cloud:rss mt` 표시.
- **남은 확인**: 분 배율·실제 월 소모량은 며칠 운영 후 Settings > Billing 에서 사용자가 확인(daily 회차는 2분 과금 추정, snapshot·news 회차는 더 짧을 것). 월 소모가 1,500분을 넘으면 daily 를 하루 2회로, 평일 snapshot 을 2시간 간격으로 줄인다.
- **GitHub 등록 함정**: 저장소 첫 푸시 때 `collect.yml` 이 워크플로로 등록되지 않았다(목록 0개, 스케줄·dispatch 전용 파일은 오류도 표시되지 않음). 다른 워크플로 파일은 즉시 등록되므로 문법 문제가 아니라 첫 푸시 등록 실패였고, **파일 내용을 바꿔 다시 푸시하자 등록됨**. 워크플로가 안 보이면 파일을 한 줄 수정해 푸시한다.
- 개인정보: 푸시 전 점검 — 추적 파일 94개·전체 히스토리에 `.env` 값 0건, `.env`·`*.session`·`data/*.db` 는 `.gitignore`. 텔레그램 메시지가 보이던 스크린샷(`phase5_*` 3장, `phase6_report_news_events_sources`)은 **히스토리에서 제거**했다(사용자 승인, 되돌리기용 `backups/pre-screenshot-removal.bundle` 은 로컬 전용·gitignore). 이후 텔레그램 내용이 보이는 캡처는 저장소에 넣지 않는다.

### 리포트 자동 생성·따라잡기 (2026-10-10)
- 코드 `marketview/report/auto.py`(앱 수집 사이클마다 호출, `app._maintenance`), 마이그레이션 `005_report_origin.sql`(`report_run.origin` = manual|auto|catchup). 설정은 리포트 패널의 "자동 생성"(`app_setting.report_auto`, 기본 켬).
- **규칙**: ① 오늘 리포트 — 평일, KST 07:30 이후, 오늘 리포트가 없을 때 `auto`. ② 꺼져 있던 날 — 기능을 처음 평가한 날(또는 다시 켠 날, `sync_state.report_auto_since`) 이후의 **평일** 중 리포트가 없는 날을 날짜별로 `catchup`, 한 번에 최근 7일까지. ③ 수동 생성을 포함해 그 날짜의 `report_run` 행이 하나라도 있으면 다시 만들지 않음(덮어쓰기·-v2 폭주 없음). ④ 한 날짜가 3번 실패하면 포기하고 패널에 사유 표시. ⑤ 다른 생성(수동 버튼)이 진행 중이면 그 주기는 건너뜀. 수집·화면과 독립.
- **사후 생성의 정확성**: 지난 날짜 리포트는 그 날짜까지의 일별 종가로 시세·전일 대비·차트를 만들고, 포지션도 `list_positions(asof)` 로 그 날짜 시점(진입 전 포지션 제외, 이후에 청산된 것은 열린 것으로)으로 계산한다. 뉴스·텔레그램은 **그 시점에 수집되어 있던 것**만 담기므로 앱이 꺼져 있던 시간대의 기사는 빠질 수 있고(클라우드 수집분이 병합돼 있으면 채워짐), 리포트 상단에 그 사실을 명시한다. 오늘 리포트도 이제 일별 종가 기준(장중 값 제외)이다.
- 한계: 한국 공휴일 달력이 없어 휴장일 평일도 만든다(값은 직전 영업일 그대로). 앱이 아예 켜지지 않으면 생성되지 않는다(다음 실행 때 따라잡기).
- 실행 증거(실제 DB 복사본 + 임시 폴더): 기준일을 10-06 으로 두고 서버 시작 → 수집 후 10-06~10-09 4일치 `catchup` 리포트(PDF·Drive 사본 ok) 자동 생성, 토요일인 오늘은 생성 안 함, 날짜별 값이 다름(국고 3년 3.933→3.961→3.983%), 설정 토글 동작.

### 남은 확인 필요 (다음 Phase 또는 사용자 결정)
0. **Phase 6**: (a) 실제 `G:` Drive 로의 복사는 아직 실행하지 않음(시험은 임시 폴더) — 사용자가 첫 리포트를 만들며 확인. (b) PDF 래스터화 도구(poppler)가 없어 PDF 를 이미지로 확인하지 못함 → 사용자가 PDF 를 열어 한글·레이아웃 확인 요청. (c) ~~꺼져 있던 날 자동 생성~~ → 구현됨(아래 "리포트 자동 생성"). (d) 뉴스 3줄 요약(ANTHROPIC_API_KEY)은 미구현이라 "요약 기능 on" 분기 없음. (e) 파생 값 출처는 요약표에서 "파생"으로만 표기(구성 값의 출처는 차트 하단에 표기).
1. **국내 크레딧 섹터(공사채·은행채·카드채·여전채·회사채 AAA)**: ECOS에 없음(AA-/BBB-만). 금투협 채권정보센터는 WebSquare(JS) 앱이라 내부 API를 추측하지 않음 → **CSV 붙여넣기 수동 입력 경로**를 Phase 4에서 구현. 자동화하려면 사이트 네트워크 호출 분석이 필요(별도 요청 시).
2. ~~KRX 지수 API~~ 완료(2026-10-14): KOSPI·KOSDAQ 연결.
3. ~~KRX 국채선물 공식 상품명세 대조~~ 완료(2026-10-14).
4. 한국 CDS 5Y(수동), NDF 1M(수동 유지 결정), USD SOFR 스왑 커브(무료 소스 못 찾음 → 수동), 미국채선물 *커브*(수익률 아님, 가격만).
5. 국고채 입찰일·한국 CPI 일정의 자동 소스 (재정경제부 월별 발행계획 PDF 판독 또는 국채시장 사이트) — 지금은 수동 추가. FOMC 2027 일정은 연준이 잠정이라고 표시.
6. 텔레그램: api_id/api_hash 발급·최초 로그인·CHANNELS 작성(사용자), telethon 설치 승인, 첨부 PDF 직접 열기(로그인 후 검증하며 추가).
7. ~~GitHub Actions 무료 분량~~ 문서 확인(2026-10-10 §11) + 실측 추정 월 200~250분(2026-10-15). 공식 소모량은 사용자가 Billing 에서 확인.
8. ECOS `bok_base_rate`는 주말에도 일별 행이 온다(1095행) — 소스 특성, 그대로 저장.

## 15. 완료 기록 (CLAUDE.md 에서 옮김, 2026-10-14)

- **폴더 이동 완료·검증됨 (2026-10-11)**: 사용자 폴더 아래로 이동, .venv 정상(Python 3.14.7), pytest 186개 통과, origin 그대로, 바탕화면 `MarketView.lnk` 가 새 `scripts\start.bat` 를 가리킴. 이동으로 `report_run` 에 저장된 옛 절대경로가 깨져 `/api/report/file` 이 404 → 현재 reports 폴더 기준으로 다시 찾도록 수정(DB 행은 그대로).
- **클라우드 스케줄 정상 동작 확인(2026-10-11)**: `event=schedule` 회차 10회 모두 success. 최신 status.json 은 `partial`(연합뉴스 RSS 2건 타임아웃) — 이전 `mt` 타임아웃은 사라졌고 RSS 타임아웃은 회차마다 다른 소스에서 가끔 난다. 반복되는 소스가 생기면 클라우드만 제한 시간 연장·재시도.
- **자동 리포트 확인 완료(2026-10-14)**: `origin=auto` 가 10-12·10-13·10-14 평일마다 1건씩 생성(pdf·drive ok, 중복 없음). `catchup` 은 꺼져 있던 평일이 없어 아직 발동 전.
- 사용자가 Drive 에 리포트 md·pdf 가 생성된 것을 확인함(Phase 6 확인 완료).
- **KRX 키 교체(2026-10-14)**: 기존 키가 폐기되어 401 → 새 키로 `.env`·GitHub Secret `KRX_AUTH_KEY` 교체, 클라우드 daily 94/94 ok. 활용신청 승인으로 `idx/kospi_dd_trd`·`idx/kosdaq_dd_trd` 200 확인(`idx/krx_dd_trd` 는 미신청 401). KOSPI 는 `IDX_NM=="코스피"` 행의 `CLSPRC_IDX`(빈 문자열 = 미수집, 0 으로 채우지 않음). **KOSPI·KOSDAQ KRX 연결 완료(같은 날)**: 일별 KRX 우선, Yahoo 는 KRX 게시 전 날짜 대체+장중, `db.upsert_observation` 이 KRX 를 Yahoo 가 덮지 못하게 막음. KOSDAQ 타일 신규(주식 탭). Secret 을 `gh secret set` 에 넘길 땐 PowerShell 파이프 대신 `cmd /c "... < 파일"`(BOM 방지, ASCII 파일).
- **수집 속도·표시 개선 완료(2026-10-14)**: 수집 한 사이클 ~54초 → ~10초(사용자가 15초 이내 확인). ① `ingest.fetch_parallel` 로 소스별 동시 호출(ecos 4·fred 8·yahoo 3·krx 3·smbs 1), DB 쓰기는 한 스레드 ② `app._collect` 가 일별·이벤트·스냅샷·뉴스 단계를 동시에 실행(단계마다 연결 따로) ③ 텔레그램은 연결 한 번으로 채널 동시 읽기(세션 파일은 클라이언트 하나만 쓸 수 있다) ④ KRX 범위 수집에 40초 시간 예산 ⑤ 수집 중에는 화면이 1.5초마다 상태 확인, 서버가 꺼지면 새로고침 버튼이 "서버 꺼짐" ⑥ `index.html` 이 `app.js`·`app.css` 주소에 파일 수정 시각(`?v=`)을 붙여 캐시된 옛 코드를 쓰지 않는다(옛 `app.js` 캐시가 "수집 중이 오래 간다"의 원인이었다).
- **현재 화면 구성(2026-10-14)**: 마감 기준 단일 모드(권역별 마감일 확정값, 메인 패널 오른쪽 위, 지표 검색 아이콘 왼쪽에 작게 'KR MM.DD US MM.DD' 표시), 주식 탭에 KOSDAQ, 국채선물 공식 명세 대조 완료, 기준금리는 `step` 지표라 "이전 값" 표시에서 제외. 증권사(KIS) API·실시간은 보류(검토했으나 계좌·키 보안 부담 대비 이득이 작아 안 함). 장중 스냅샷 수집은 남아 있으나 화면·리포트 미사용.
- **2026-10-14 사용자 결정**: 뉴스 3줄 요약·텔레그램 PDF 열기·한국어 기사 필터·KIS 연동은 **하지 않는다**(보류가 아니라 취소). 장중 스냅샷은 **중단·삭제**(실제 DB 의 intraday 828행 삭제, 되돌리기용 `backups/pre-intraday-delete.db` 로컬 전용·gitignore; cloud-data 의 snapshots.json 도 제거, cloudsync 는 snapshots 파일을 무시, queries 의 장중 값 사용 코드 제거. `observation_log`·`ingest_run` 이력은 append-only 라 그대로). 수집 호출 중단: (로컬 `_collect`·클라우드 `cloud.collect` 에서 `run_snapshot` 호출 제거, 워크플로 시간별 회차는 `news` 모드; `snapshot` 모드 이름은 옛 회차 호환용 별칭). RSS 타임아웃 대응: 피드 읽기 25초·재시도 3번(백오프 2·4·5초, `collect/rss.py` `FEED_TIMEOUT/FEED_RETRIES`, `base.get(retries=)`).
- **최적화 점검 완료(2026-10-14)**: 읽기 API 는 전부 20ms 이하·인덱스 정상이라 손대지 않았다. `cloud._merge_rows` 가 값이 같은 행을 `captured_at` 만 바뀌었다고 다시 쓰던 것을 고쳐(커밋 065ce53) 같은 값의 재수집은 `pages.json` 을 바꾸지 않는다(`cloud-data` 의 무의미한 diff 와 `test_collect_twice_same_result` 의 간헐 실패 해소). 클라우드 `news`·`daily` 회차 수동 실행과 복사본 DB 병합(멱등·수동 값 보호)까지 확인. 다음 `daily` 회차에서 값이 안 바뀐 행이 `pages.json` 에 diff 를 만들지 않는지 한 번 더 본다. `ubuntu-latest` 는 2026-10-19 부터 Ubuntu 26 으로 바뀌므로 그 뒤 첫 회차가 도는지 확인(문제 시 `ubuntu-24.04` 고정).

- **리뷰·수정 기록 (2026-10-15, 커밋 70bbba1 · fde8ed3 · 7cd77e6 · c03367a · 523f8cf, 테스트 233개)**:
  - 데이터 오류: 수동 CSV 가 탭 구분 붙여넣기의 천 단위 쉼표(`1,385.20`)를 쪼개 `ndf_1m=1.0` 으로 저장하던 것 → 탭을 구분자로 쓰고 남는 칸은 거부.
  - 뉴스 중복 제거: 전 기간 `MIN(id)` 비교라 매일 같은 제목의 기사가 첫날 이후 사라지고 다른 탭의 행이 가리던 것 → 필터를 통과한 행끼리 12시간 창 안에서만 합침.
  - 클라우드 병합: 파일·행 단위 격리, 로컬 `series_def` 에 없는 키(외래키 위반)는 건너뛰고 `cloud-merge` 경고로 표시, 건너뛴 파일은 다음 회차 재시도.
  - 보안·운영: 교차 출처 쓰기 차단(Origin·Sec-Fetch-Site), `partial` 수집도 1시간 뒤 재시도, 리포트 API 락 누수, 런처가 첫 화면 제목으로 MarketView 확인.
  - 수집: smbs 연속 실패만 집계·기준일 못 읽으면 `Failed`(구조 변경), Yahoo 장중 전용 항목 제거, KRX/Yahoo 주석 정리. 텔레그램 수정본을 화면·리포트에 표시. 리포트 '시작일=100' 차트는 공통 시작일.
  - 클라우드: `cloud-data` 보존 45일 + 커밋 250개 초과 시 squash(`cloud_housekeeping.py`), mt 클라우드 제외(연결 단계 차단, 실패 74~77초→제외 후 5초).

- **2차 리뷰 수정 (2026-10-15, 테스트 243개)**:
  - 화면: 창을 켜 둔 채 자정을 넘기면 `meta.today` 가 어제로 남아 이벤트 D-day 가 하루 밀리고 리포트 패널 기본 날짜가 어제(→ 어제 -v2 생성)였다 → 시계가 KST 날짜 변경을 감지하면 `/api/meta` 를 다시 받고 이벤트·화면을 다시 그린다(`app.js` `onNewDay`).
  - 백필 빈 구간: 시작점이 '마지막 관측일 − 7일' 뿐이라, 앱을 오래(≈55일 이상) 꺼 두면 클라우드 병합이 최신 값만 채워 그 앞 구간을 영영 받지 않았다 → `ingest._start_for` 가 끝이 최근 120일 안에 있는 14일 초과 빈 구간(출처 무관)을 찾아 그 앞부터 다시 받는다. 가장 긴 연휴 간격 8일(2025 추석)은 빈 구간이 아니다. 실제 DB 46개 수집 대상의 시작점은 바뀌지 않음(읽기 전용 확인).
  - Drive 재시도: Drive 가 없는 동안 15분마다 VACUUM·CSV 전체 내보내기를 되풀이하고 CSV 가 주간이 아니라 날마다 쌓였다 → Drive 폴더가 다시 보일 때만 재시도, CSV 는 다시 내보내지 않고 이미 만든 파일만 복사(`backup.retry_csv_drive`).
  - 일부 실패 숨김: KRX·smbs 가 일부 날짜만 실패해도 받은 행이 있으면 `ok` 로 기록되어 경고·1시간 재시도가 없었다 → 받은 행은 저장하되 `error`("수신 N행 · 일부 실패 …") 로 기록.
  - (같은 날, 테스트 247개) 워크플로의 API 키 3개를 잡 전체 `env` 에서 수집 단계 `env` 로 옮김(`pip install` 등 다른 단계는 키를 못 본다). 마이그레이션 파일 하나를 한 트랜잭션으로 적용(중간 실패 시 통째로 되돌리고 `schema_version` 미기록 → 고친 뒤 다시 적용). 월별 백업·Drive 사본(일일·월별·CSV)을 `.part` 로 복사한 뒤 이름을 바꿈(끊겨도 잘린 파일이 생기지 않고 기존 월별 영구본 유지). 리포트 6절 각주를 "기준일까지의 마지막 일별 종가" 로 정정.