# MarketView

> **English summary.** A personal morning market monitor for KRW and USD rates, FX and credit. It puts KTB yields, KRW IRS, KTB futures, UST, USD/KRW and credit spreads on one screen and writes a daily report every weekday. The problem it solves is timing: Korean and US sources publish on different lags, so a naive screen quietly mixes today's and last week's numbers in one spread. MarketView aligns every value to its region's last confirmed close and shows the source and observation date next to each number. About 36,000 daily observations since 2023, 18 daily reports since October 2026.

금리·FX·크레딧 시장을 매일 아침 같은 기준으로 점검하려고 만든 개인용 마켓 모니터입니다. 국고채, KRW IRS, 국채선물, UST, USD/KRW, 크레딧 스프레드를 한 화면에 모으고, 평일마다 일일 리포트를 남깁니다.

![채권 탭: 국고 3·10년과 10−3 스프레드](docs/screenshots/main_bond.png)

## 한눈에 보기

| | |
|---|---|
| **무엇을** | 매일 아침 확인하는 금리·FX·크레딧·주식 지표를 한 화면과 일일 리포트로 정리 |
| **풀려던 문제** | 소스마다 게시 시점이 달라, 오늘 값과 며칠 전 값이 같은 줄에 섞이면 스프레드와 전일 대비가 틀려도 티가 나지 않음 |
| **해결** | 한국·미국 권역별로 마지막 확정일을 정하고 같은 날짜의 값끼리만 비교. 숫자마다 출처와 관측일 표시 |
| **규모** | 지표 81개 정의(57개 자동 수집), 일별 관측치 36,314행(2023-10 ~ 2026-11), 일일 리포트 18건 |
| **역할** | 볼 지표, 비교 기준, 화면 구성을 직접 정하고 Claude Code로 구현 |

## 매일 아침 보는 것

| 탭 | 지표 | 함께 보는 스프레드 |
|---|---|---|
| 채권 | 기준금리, KOFR, CD·CP 91일, 국고 3·5·10·30년, 국채선물 3·10·30년(내재수익률·미결제약정), KRW IRS 1~20년, UST 2·5·10·30년, SOFR | 국고 10−3, 국고 30−10, CD−KOFR, UST 10−2, UST 30−10 |
| 외환 | USD/KRW, USD/JPY, EUR/USD, DXY | |
| 신용 | 회사채 AA-·BBB- 3년, 미국 IG·HY OAS | 회사채 − 국고 3년 |
| 주식 | KOSPI, KOSDAQ, S&P 500, NASDAQ, Russell 2000, VIX | |

커브는 기준일, 1주 전, 1개월 전을 겹쳐 그리고 아래에 구간별 bp 변화를 붙였습니다. 오른쪽에는 탭별로 분류한 뉴스와 FOMC·금통위·미국 지표 일정(D-day, 한국 시각)을 둡니다.

| 국고채 커브 (기준일 / 1주 전 / 1개월 전, 아래는 bp 변화) | KRW IRS 커브 |
|---|---|
| ![국고채 커브](docs/screenshots/ktb_curve.png) | ![KRW IRS 커브](docs/screenshots/irs_curve.png) |

## 핵심 문제: 같은 날짜의 숫자끼리 비교하기

![소스별 최신 관측일](docs/figures/data_timing.png)

2026-11-02(월) 아침에 만든 리포트입니다. 한국 소스는 10/30(금)이 마지막 값이고, FRED의 미 국채는 10/29(목)까지만 올라와 있었습니다. 소스마다 가장 최신 값을 그냥 띄우면 서로 다른 날의 숫자가 한 화면에서 비교됩니다. 한국과 미국을 하나의 날짜로 맞추면 늦게 올라오는 미국에 맞추느라 한국의 최신 값을 버려야 합니다.

그래서 권역별 마감일을 씁니다. 한국 지표는 한국 마감일, 미국 지표는 미국 마감일 기준으로 보여 주고, 그보다 오래된 값은 날짜를 굵게 표시합니다. 스프레드는 두 값의 관측일이 같을 때만 계산합니다. 값을 받지 못하면 0이나 전일 값으로 채우지 않고 "미수집"으로 띄웁니다.

## 일일 리포트

평일 07:30 이후 앱이 켜지면 리포트(Markdown·PDF)를 자동으로 만들고 Google Drive에 복사합니다. 앱이 꺼져 있던 날은 다음 실행 때 그날 종가 기준으로 따라잡습니다.

리포트에는 탭별 요약 표, 커브·스프레드 차트 6개, 탭별 뉴스와 증권사 리서치 채널 제목, 2주 안의 이벤트, 페이퍼 포지션, 데이터 출처와 수집 실패 목록이 들어갑니다. 아래는 2026-11-02 리포트의 국고채 부분입니다.

> 데이터 기준: 국내 2026-10-30 / 미국 2026-10-29
>
> | 지표 | 레벨 | 전일 대비 | 관측일 | 출처 |
> | :-- | --: | --: | :-: | :-- |
> | 국고 3년 | 3.715% | −5.1bp | 2026-10-30 | ECOS |
> | 국고 10년 | 4.008% | −7.3bp | 2026-10-30 | ECOS |
> | 국고 30년 | 4.124% | −8.8bp | 2026-10-30 | ECOS |
> | 국고 10−3 | 29.3bp | −2.2bp | 2026-10-30 | 파생 |
> | 국채선물 10년 내재수익률 | 4.016% | −6.9bp | 2026-10-30 | 파생 |
> | KRW IRS 5년 | 3.7825% | −4.7bp | 2026-10-30 | 서울외국환중개 |
> | UST 10년 | 4.98% | −7.0bp | 2026-10-29 | FRED |

## 트레이딩 관점에서 정한 규칙

**포지션 손익의 방향.** 금리 항목은 채권 관행을 따릅니다. "국고 10년 롱"은 채권 롱이고 금리가 내려야 이익입니다. 스프레드, 가격, 환율은 레벨 기준입니다. 금리를 레벨 기준으로 두면 "국고 10년 롱"이 금리 상승 베팅으로 읽히기 때문입니다. 페이퍼 포지션의 손익, 목표·손절 경고, 리포트 문구가 모두 이 규칙 하나를 따릅니다.

**국채선물 내재수익률.** 선물 가격에서 표면금리 5%, 6개월 이표 표준물 기준으로 수익률을 역산합니다. 국채선물은 현금결제라 CTD가 없고, 이 수익률은 기초채권 바스켓의 평균수익률과 비교할 값입니다. 국고 지표물과는 바스켓 만기 구성과 캐리만큼 차이가 납니다. 3년물 기준 이 차이는 10월 초 약 12bp에서 10/30 약 4bp로 줄었습니다.

**KRW IRS 출처.** 서울외국환중개 IRS 표의 Mid를 씁니다. 과거 날짜 조회가 되는 것을 확인해 1년치를 채웠습니다.

**무료 소스가 없는 지표.** 한국 CDS 5Y와 USD/KRW NDF 1M은 무료 API가 없습니다. 화면에서 빼지 않고 "소스 없음 · 수동 입력"으로 띄워, 무엇을 못 보고 있는지 알 수 있게 했습니다.

## 운영 기록

2026-11-03 백업 기준입니다.

| 항목 | 값 |
|---|---|
| 수집 실행 | 357회 (성공 343 · 일부 실패 1 · 실패 13), 2026-10-09부터 |
| 지표별 수집 결과 | 9,746건 중 성공 9,733 |
| 일별 관측치 | 36,314행, 57개 지표 (2023-10-10 ~ 2026-11-02) |
| 뉴스 | 41,464건 (제목·링크·요약만 저장) |
| 일일 리포트 | 18건 (자동 11 · 따라잡기 6 · 수동 1) |

## 한계

- 국내 크레딧은 회사채 AA-·BBB- 3년만 자동 수집합니다. 은행채·여전채 같은 섹터 스프레드는 무료 API가 없어 넣지 않았습니다.
- 한국 공휴일 달력이 없어 휴장일 평일에도 리포트가 생성됩니다(값은 직전 영업일).
- 국채선물은 거래량이 가장 많은 월물을 쓰므로 월물 교체일에 가격과 내재수익률이 불연속으로 움직입니다.
- 노트북이 꺼진 동안 뉴스는 GitHub Actions가 받는데, 무료 스케줄이 늦게 돌아 간격이 약 4시간입니다.

## 만든 과정

2026년 8월에 만든 앞 버전을 두 달 쓰면서 불편했던 점을 정리해 새로 만들었습니다. Notion의 Market Notes 두 편(2026년 8~9월)은 앞 버전으로 모은 데이터로 썼습니다. 처음 적은 화면 스케치는 [설계도.txt](설계도.txt), 요구사항 원문은 [MarketView_프롬프트.md](MarketView_프롬프트.md), 설계 문서는 [SPEC.md](SPEC.md)입니다.

<details>
<summary><b>구현 세부 (데이터 소스, 구조, 실행 방법, 검증)</b></summary>

### 데이터 소스

| 소스 | 내용 | 기간 |
|---|---|---|
| ECOS (한국은행) | 기준금리, KOFR, CD·CP 91일, 국고 1~30년, 회사채 AA-·BBB- 3년, USD/KRW | 3년 백필 |
| 서울외국환중개 | KRW IRS 1~20년 Mid | 1년 백필 |
| KRX | 국채선물 3·10·30년 종가·미결제약정, KOSPI·KOSDAQ | 1년 백필 |
| FRED | UST 1개월~30년, SOFR, IG·HY OAS, VIX, S&P 500·NASDAQ | 3년 백필 |
| Yahoo (비공식) | DXY, WTI 근월물, 미국채선물 | 모듈 단위로 격리 |
| 수동 입력 | 한국 CDS 5Y, NDF 1M | 무료 소스 없음 |

API 코드(ECOS 통계표·항목, FRED 시리즈, KRX 상품명)는 실제 호출로 확인한 것만 넣고, 확인 날짜를 `marketview/config/sources.py` 주석에 남겼습니다. 서울외국환중개 IRS 표는 값이 16진 문자코드로 인코딩되어 있어 디코딩 파서를 만들었습니다(`collect/smbs.py`).

### 구조

```mermaid
flowchart LR
    subgraph Sources[소스]
      E[ECOS]
      F[FRED]
      K[KRX]
      S[서울외국환중개]
      Y[Yahoo]
      R[RSS]
    end
    Sources --> C[marketview/collect/*<br/>Fetched · Missing · Failed]
    C --> I[ingest.py<br/>값이 있을 때만 저장]
    GA[GitHub Actions<br/>뉴스·당일 페이지] -->|cloud-data 브랜치| CS[cloudsync.py<br/>읽기 전용 병합]
    I --> DB[(SQLite WAL<br/>observation + append-only log)]
    CS --> DB
    DB --> Q[queries.py · derived.py<br/>권역 마감일 · 스프레드 조회 시 계산]
    Q --> UI[FastAPI + 순수 JS 화면]
    Q --> RP[report/<br/>Markdown · PDF · Drive 복사]
    DB --> BK[backup.py<br/>일일 30개 · 월말 영구 · 주간 CSV]
```

```
marketview/
├── collect/      소스별 수집기 (로컬과 Actions가 같은 코드를 쓴다)
├── config/       지표 정의, 소스 코드(확인일 주석), 화면 구성, 뉴스 피드, 이벤트 출처
├── ingest.py     일별 수집·백필, 빈 구간 재수집
├── queries.py    타일·시계열·커브 조회, 권역 마감일 (common_close_dates)
├── derived.py    파생 스프레드 레지스트리 (관측일이 같은 값끼리만 계산)
├── futures.py    국채선물 내재수익률 (KRX 공식 상품명세로 대조)
├── userdata.py   이벤트·포지션 (append-only 이력, profit_sign)
├── report/       일일 리포트 생성, 자동 생성·따라잡기
├── backup.py     VACUUM INTO 스냅샷 → 무결성 검사 → gzip, 복원
└── cloud*.py     Actions 수집과 로컬 병합
migrations/       001~005 SQL
tests/            253개, 네트워크 없이 저장된 응답과 임시 DB로 돈다
```

**노트북이 꺼져 있을 때.** 일별 시세는 소스가 과거를 보관하므로 앱이 켜질 때 로컬이 백필합니다. 나중에 다시 구할 수 없는 것(뉴스, 당일 값만 보이는 페이지)만 GitHub Actions가 같은 수집 코드로 받아 별도 브랜치에 커밋하고, 로컬 앱이 읽기 전용으로 병합합니다. 같은 파일을 여러 번 합쳐도 결과가 같습니다.

### 실행 방법

Windows, Python 3.14 (테스트는 3.13에서도 통과). Node·ORM·빌드 단계는 없습니다.

```
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          # ECOS·FRED·KRX 키. 비어 있으면 그 소스만 건너뛴다
.venv\Scripts\python -m uvicorn marketview.app:app --host 127.0.0.1 --port 8790
```

`127.0.0.1:8790`을 엽니다. 처음 실행하면 소스별로 가능한 기간을 백필합니다. 바탕화면 아이콘은 `scripts\install_shortcut.ps1`로 만듭니다.

```
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m marketview.report 2026-11-02     # 리포트 수동 생성
.venv\Scripts\python -m marketview.backup all             # 백업 수동 실행
```

백업·복원 절차와 보관 규칙은 SPEC.md §10, 클라우드 수집 구조는 §6·§11에 있습니다.

### 공개본에서 뺀 것

- API 키(`.env`), DB·리포트·백업 파일, 텔레그램 세션.
- 구독 중인 텔레그램 채널 목록(`config/telegram.py`는 비워 두었습니다).
- Actions 스케줄. 이 저장소에서는 수동 실행만 남겼고, 실제 수집은 비공개 저장소에서 돕니다.
- 개인 환경 경로와 운영 기록.

### 개발 방식과 검증

화면 스케치와 요구사항은 직접 정했고, 설계 문서와 운영 규칙([CLAUDE.md](CLAUDE.md))을 먼저 쓴 뒤 Claude Code로 단계별(Phase 0~8)로 구현했습니다. 단계마다 서버 로그, 스크린샷, 생성된 리포트로 동작을 확인한 뒤 다음 단계로 넘어갔고, 화면 구성·기준 날짜·포지션 방향 같은 결정은 SPEC.md §13에 날짜와 함께 남겼습니다.

- 소스 코드는 실제 호출로 확인한 것만 넣고 확인일을 주석에 남겼습니다.
- 수집기·파서 테스트는 저장된 응답으로 네트워크 없이 돕니다. 병합은 같은 입력을 두 번 넣어도 결과가 같은지, 수동 입력값을 자동 수집이 덮지 않는지 테스트합니다.
- 백업은 실제 DB로 백업 → 복원 → 표별 행 수 비교까지 해 봤습니다.
- 공개 전에 저장소 전체에서 API 키·토큰·개인 경로를 검색했습니다.

</details>

## License

MIT. 번들한 서드파티 파일은 각자의 라이선스를 따릅니다: Apache ECharts (Apache-2.0, `marketview/static/vendor/`), Pretendard (SIL OFL 1.1, `marketview/static/fonts/`).
