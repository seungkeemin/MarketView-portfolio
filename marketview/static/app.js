
// ---- 저장된 UI 상태 (localStorage 는 없어도 동작해야 한다) -----------------
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 무시 */ } },
};
const ui = Object.assign({
  tab: "bond", group: {}, sub: "ts", range: "1Y", family: "ktb", newsTab: "bond", colors: "kr",
  sel: {}, cols: 70, views: 18, events: 60, cmp1: "", cmp2: "",
}, LS.get("mv.ui", {}));
const save = () => LS.set("mv.ui", ui);
const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const PALETTE = () => ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"].map(css);

let meta = null;          // /api/meta
let tilesData = null;     // /api/tiles 응답 (현재 탭)
let asof = "";            // 과거 기준일 (빈 값 = 최신)
let renderSeq = 0;        // 늦게 도착한 응답이 최신 화면을 덮어쓰지 않게 한다

const fmtN = (v, d) => (v == null ? "—" : Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d }));
const dirClass = (x) => (x > 0 ? "up" : x < 0 ? "down" : "flat");
const safeUrl = (u) => (/^https?:\/\//i.test(String(u)) ? String(u) : "#");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch { /* 본문 없음 */ }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}
const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null)).toString();

// ---- 시계 · 장 상태 --------------------------------------------------------
const tzFmt = (tz, opts) => new Intl.DateTimeFormat("ko-KR", { timeZone: tz, hour12: false, ...opts });
const kstDate = tzFmt("Asia/Seoul", { year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" });
const kstTime = tzFmt("Asia/Seoul", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const nyFmt = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hourCycle: "h23", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
const nyTime = {
  format(d) {  // HH:MM:SS EDT/EST (서머타임 자동)
    const o = {};
    for (const p of nyFmt.formatToParts(d)) o[p.type] = p.value;
    return `${o.hour}:${o.minute}:${o.second} ${o.timeZoneName}`;
  },
};
function parts(tz, d) {
  const o = {};
  for (const p of new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", weekday: "short" }).formatToParts(d)) o[p.type] = p.value;
  return o;
}
function marketOpen(tz, oh, om, ch, cm, d) {
  const p = parts(tz, d);
  if (p.weekday === "Sat" || p.weekday === "Sun") return false;
  const m = Number(p.hour) * 60 + Number(p.minute);
  return m >= oh * 60 + om && m < ch * 60 + cm;
}
const kstYmd = (d) => { const p = parts("Asia/Seoul", d); return `${p.year}-${p.month}-${p.day}`; };
function tickClock() {
  const now = new Date();
  $("kst-date").textContent = kstDate.format(now).replace(/\.\s*/g, ".").replace(/\.\(/, "(");
  $("kst-time").textContent = kstTime.format(now);
  $("ny-time").textContent = nyTime.format(now);
  for (const [id, open] of [["kr-mkt", marketOpen("Asia/Seoul", 9, 0, 15, 30, now)], ["us-mkt", marketOpen("America/New_York", 9, 30, 16, 0, now)]]) {
    const el = $(id);
    el.textContent = (id === "kr-mkt" ? "국내 " : "미국 ") + (open ? "장중" : "마감");
    el.classList.toggle("open", open);
    el.title = "정규장 시간만 반영합니다. 공휴일·휴장일은 반영하지 않습니다.";
  }
  if (meta && !dayRolling && kstYmd(now) !== meta.today && Date.now() - dayRollAt > 30000) onNewDay();
}
// 창을 켜 둔 채 자정을 넘기면 처음 받은 meta.today(이벤트 D-day·리포트 기본 날짜·기준일 달력 상한)가 어제로 남는다 → 다시 받는다
let dayRolling = false, dayRollAt = 0;
async function onNewDay() {
  dayRolling = true;
  dayRollAt = Date.now();               // 서버가 꺼져 있으면 30초 뒤 다시 시도
  try {
    meta = await api("/api/meta");
    $("asof").max = meta.today;
    renderEvents();
    if (!asof) { renderMain({ skeleton: false }); renderPositions(); renderNews(); }
  } catch { /* 다음 시도는 dayRollAt 기준 */ }
  dayRolling = false;
}

// ---- 탭 · 그룹 · 선택 ------------------------------------------------------
const tabMeta = () => meta.tabs.find((t) => t.id === ui.tab);
function currentGroup() {
  const groups = tabMeta().groups;
  return groups.find((g) => g.id === ui.group[ui.tab]) || groups[0];
}
function selection() {
  return (ui.sel[ui.tab] && ui.sel[ui.tab].length) ? ui.sel[ui.tab] : tabMeta().default_sel;
}

function renderTabs() {
  $("main-tabs").innerHTML = meta.tabs.map((t) => `<button data-tab="${t.id}" class="${t.id === ui.tab ? "on" : ""}">${t.label}</button>`).join("");
  const tm = tabMeta(), cg = currentGroup();
  const showChips = tm.groups.length > 1;
  $("group-chips").hidden = !showChips;
  $("group-chips").innerHTML = tm.groups.map((g) => `<button data-group="${g.id}" class="${g.id === cg.id ? "on" : ""}">${g.label}</button>`).join("");
  const hasCurve = ui.tab === "bond";
  if (!hasCurve && ui.sub === "curve") ui.sub = "ts";
  $("sub-tabs").innerHTML = [["ts", "시계열"], ["curve", "커브"], ["table", "데이터 표"]].map(([id, l]) =>
    `<button data-sub="${id}" class="${id === ui.sub ? "on" : ""}" ${id === "curve" && !hasCurve ? 'disabled title="이 탭에는 커브가 없습니다"' : ""}>${l}</button>`).join("");
  $("ranges").innerHTML = ["1M", "3M", "6M", "1Y", "3Y", "전체"].map((r) => `<button data-range="${r}" class="${r === ui.range ? "on" : ""}">${r}</button>`).join("");
  $("ranges").hidden = ui.sub === "curve";
  $("csv-btn").hidden = ui.sub !== "table";
  $("curve-controls").hidden = ui.sub !== "curve";
  $("chart-hint").textContent = ui.sub === "ts" ? "Ctrl+클릭: 겹쳐 보기 · 드래그: 확대" : "";
}

// ---- 타일 ------------------------------------------------------------------
function changeText(s, decimals) {
  if (s.change == null) return "";
  const sign = s.change > 0 ? "+" : s.change < 0 ? "−" : "";
  const mag = Math.abs(s.change);
  if (s.change_unit === "bp") return `${sign}${mag.toFixed(1)}bp`;
  if (s.change_unit === "%") return `${sign}${mag.toFixed(2)}%`;
  // 값과 같은 소수자리 (EUR/USD +0.0001 이 +0.00 으로 보이던 문제, 리포트 fmt_change 와 같은 기준)
  return `${sign}${fmtN(mag, mag >= 1000 ? 0 : decimals)}`;
}
function tileHtml(t) {
  const s = t.state;
  const sel = selection().includes(t.key) ? " sel" : "";
  if (!s) {
    const why = t.manual ? "미수집 · 클릭해서 입력" : t.derived ? "구성 값 미수집" : "수집된 값 없음";
    const tip = t.manual ? "소스 없음, 직접 입력" : t.derived ? "파생값은 구성 값이 같은 관측일에 모두 있어야 계산됩니다" : "수집된 값이 없습니다 · 상단 경고와 새로고침을 확인하세요";
    return `<button class="tile empty${sel}" data-k="${t.key}" title="${esc(t.label)} — ${tip}"><span class="name">${esc(t.label)}</span><span class="val">${why}</span><span class="meta">${t.manual ? "수동 입력" : t.derived ? "파생" : "—"}</span></button>`;
  }
  const unit = t.unit === "bp" ? '<span style="font-size:12px;font-weight:500"> bp</span>' : t.unit === "pct" ? "%" : "";
  const live = s.provider === "yahoo" ? "" : s.lag;      // Yahoo 는 출처 표기('Yahoo(비공식)')까지만 보인다 (툴팁에는 지연이 남는다)
  const stale = !!(t.ref_date && s.obs_date < t.ref_date);        // 마감 모드에서 그 권역 마감일보다 오래된 값
  const metaText = `<span class="d">${s.obs_date.slice(5)}</span> · ${esc(s.src)}${live ? " · " + esc(live) : ""}`;
  const plus = t.manual ? `<span class="plus" data-input="${t.key}" title="새 값 입력">+</span>` : "";
  const staleTip = stale ? ` · ${t.region === "kr" ? "한국" : "미국"} 마감일 ${t.ref_date.slice(5)}보다 이전 값(소스 게시 지연)` : "";
  return `<button class="tile${sel}${stale ? " stale" : ""}" data-k="${t.key}" title="${esc(t.label)} — 출처 ${esc(s.src)}${s.lag ? ", 지연 " + s.lag : ""}, 관측일 ${s.obs_date}${staleTip}">
    <span class="name">${esc(t.label)}</span><span class="vc"><span class="val">${fmtN(s.value, t.decimals)}${unit}</span><span class="chg ${dirClass(s.change)}">${changeText(s, t.decimals)}</span></span>
    <span class="meta">${metaText}</span>${plus}</button>`;
}
function renderTiles(skeleton = false) {
  if (skeleton || !tilesData) {
    const n = tilesData ? currentTiles().length || 6 : 6;
    $("tiles").innerHTML = Array.from({ length: n }, () => '<div class="tile sk"><div class="skeleton" style="width:60%"></div><div class="skeleton" style="width:85%;height:16px"></div><div class="skeleton" style="width:40%"></div></div>').join("");
    return;
  }
  $("tiles").innerHTML = currentTiles().map(tileHtml).join("");
}
function currentTiles() {
  if (!tilesData) return [];
  const g = tilesData.groups.find((x) => x.id === currentGroup().id) || tilesData.groups[0];
  return g ? g.tiles : [];
}

// ---- 시계열 차트 -----------------------------------------------------------
let chart = null;
function ensureChart() {
  // 이미 그려진 차트가 있으면 인스턴스(캔버스)를 다시 만들지 않고 비워서 쓴다 — 다시 그릴 때 깜빡임과 초기화 비용이 없다
  if (chart && !chart.isDisposed() && chart.getDom().isConnected) { chart.clear(); return chart; }
  $("view").innerHTML = '<div id="chart"></div>';
  if (chart) chart.dispose();
  chart = echarts.init($("chart"), null, { renderer: "canvas" });
  return chart;
}
const RANGE_MONTHS = { "1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "전체": Infinity };
function sliceRange(s) {
  const m = RANGE_MONTHS[ui.range];
  if (!isFinite(m) || !s.dates.length) return s;
  const end = new Date(s.dates[s.dates.length - 1] + "T00:00:00Z");
  end.setUTCMonth(end.getUTCMonth() - m);
  const cut = end.toISOString().slice(0, 10);
  const i = s.dates.findIndex((d) => d >= cut);
  return { ...s, dates: s.dates.slice(i), values: s.values.slice(i) };
}
const emptyView = (msg) => { if (chart) { chart.dispose(); chart = null; } $("view").innerHTML = `<div class="empty-note">${msg}</div>`; };
const unitSuffix = (u) => (u === "pct" ? "%" : u === "bp" ? "bp" : "");
const axisName = (u) => ({ pct: "%", bp: "bp", price: "가격", index: "지수", krw: "원", contracts: "계약" }[u] || "");

async function fetchSeries(keys) {
  return api("/api/series?" + qs({ keys: keys.join(","), asof }));
}
async function renderTimeSeries(seq) {
  const keys = selection();
  const data = await fetchSeries(keys);
  if (seq !== renderSeq) return;
  const list = keys.map((k) => data[k]).filter(Boolean).map(sliceRange).filter((s) => s.dates.length);
  const missing = keys.filter((k) => !data[k] || !data[k].dates.length);
  if (!list.length) return emptyView(`선택한 항목에 수집된 값이 없습니다 · ${missing.length ? "수동 입력 항목이면 타일을 클릭해 입력하세요" : "타일을 클릭하세요"}`);
  const colors = PALETTE();
  // 축은 두 개뿐이다: 단위가 셋 이상이면 앞의 두 단위만 그리고 나머지는 알린다 (가격·계약 수 같은 서로 다른 단위가 한 축에 섞이지 않게)
  const keptUnits = [...new Set(list.map((s) => s.unit))].slice(0, 2);
  const dropped = list.filter((s) => !keptUnits.includes(s.unit));
  const plotted = list.filter((s) => keptUnits.includes(s.unit));
  const c = ensureChart();
  const units = [];
  const series = plotted.map((s, i) => {
    if (!units.includes(s.unit)) units.push(s.unit);
    return {
      name: s.label, type: "line", showSymbol: false, yAxisIndex: units.indexOf(s.unit) === 0 ? 0 : 1,
      lineStyle: { width: 1.6, color: colors[i % 6] }, itemStyle: { color: colors[i % 6] },
      data: s.dates.map((d, j) => [d, s.values[j]]), _s: s,
    };
  });
  const yAxis = [0, 1].map((i) => ({
    type: "value", scale: true, show: i === 0 || units.length > 1, position: i === 0 ? "left" : "right",
    name: units[i] ? axisName(units[i]) : "", nameTextStyle: { color: css("--muted"), align: i === 0 ? "left" : "right" },
    axisLabel: { color: css("--muted"), formatter: (v) => fmtN(v, Math.abs(v) < 20 ? 2 : 0) },
    splitLine: { show: i === 0, lineStyle: { color: "#eef0f3" } },
  }));
  c.setOption({
    animation: false, color: colors,
    grid: { left: 56, right: units.length > 1 ? 56 : 24, top: 64, bottom: 34 },
    legend: { top: 4, left: 8, textStyle: { color: css("--text") }, icon: "roundRect", itemHeight: 3, itemWidth: 14 },
    toolbox: { right: 8, top: 0, feature: { dataZoom: { yAxisIndex: "none", title: { zoom: "드래그 확대", back: "확대 되돌리기" } }, restore: { title: "초기화" } } },
    tooltip: {
      trigger: "axis", axisPointer: { type: "cross", label: { backgroundColor: "#333" } }, confine: true,
      formatter: (ps) => {
        const day = ps[0].axisValue;
        const rows = ps.map((p) => {
          const s = series[p.seriesIndex]._s;
          const v = Array.isArray(p.value) ? p.value[1] : p.value;
          return `${p.marker}${esc(p.seriesName)} <b style="float:right;margin-left:14px">${fmtN(v, s.decimals)}${unitSuffix(s.unit)}</b><br><span style="color:#9aa2af;font-size:11px;margin-left:14px">${esc(s.src)}${s.lag ? " · 지연 " + s.lag : ""} · 관측일 ${day}</span>`;
        });
        return `<div style="min-width:200px"><b>${day}</b><br>${rows.join("<br>")}</div>`;
      },
    },
    xAxis: { type: "time", axisLabel: { color: css("--muted") }, axisLine: { lineStyle: { color: "#cfd4dc" } } },
    yAxis, series,
  });
  c.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: true });
  if (dropped.length) $("chart-hint").textContent = `단위가 달라 제외: ${dropped.map((s) => s.label).join(", ")} (축은 두 단위까지)`;
}

// ---- 커브 ------------------------------------------------------------------
const addDays = (iso, n) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
function renderCurveControls() {
  $("curve-controls").innerHTML = `<select id="family">${meta.curves.map((c) => `<option value="${c.id}" ${c.id === ui.family ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select>
    <span class="lbl">비교일</span><input type="date" id="cmp1" value="${ui.cmp1 || ""}"><input type="date" id="cmp2" value="${ui.cmp2 || ""}">`;
}
async function renderCurve(seq) {
  // 한 번에 받는다: 비교일을 주면 서버가 기준일(default_date) 커브를 함께 돌려주고, 안 주면 기준일·-7일·-30일을 돌려준다
  const cmps = ui.cmp1 && ui.cmp2 ? [ui.cmp1, ui.cmp2] : [];
  let data = await api("/api/curve?" + qs({ family: ui.family, asof, dates: cmps.join(",") }));
  if (seq !== renderSeq) return;
  const base = data.default_date;
  if (!base) return emptyView("이 커브는 수집된 값이 없습니다 · 수동 입력 항목은 해당 그룹 타일에서 입력하세요");
  if (!ui.cmp1 || !ui.cmp2 || ui.cmp1 >= base || ui.cmp2 >= base) {  // 기준일이 바뀌면 비교일을 다시 잡는다
    ui.cmp1 = addDays(base, -7); ui.cmp2 = addDays(base, -30); save();
  }
  $("curve-controls").querySelector("#cmp1").value = ui.cmp1;
  $("curve-controls").querySelector("#cmp2").value = ui.cmp2;
  if (![base, ui.cmp1, ui.cmp2].every((d) => data.curves[d])) {      // 저장된 비교일이 기준일 이후라 다시 잡은 경우만 한 번 더
    data = await api("/api/curve?" + qs({ family: ui.family, asof, dates: [base, ui.cmp1, ui.cmp2].join(",") }));
    if (seq !== renderSeq) return;
  }
  const c = ensureChart();
  const colors = PALETTE();
  const [t0, t1, t2] = [base, ui.cmp1, ui.cmp2].map((d) => data.curves[d]);
  const isPrice = data.unit === "price";
  const diff = (x) => x.values.map((v, i) => (v == null || t0.values[i] == null ? null : +((t0.values[i] - v) * (isPrice ? 1 : 100)).toFixed(isPrice ? 3 : 1)));
  const line = (name, cv, color, w) => ({ name, type: "line", xAxisIndex: 0, yAxisIndex: 0, data: cv.values, symbolSize: 5, connectNulls: false, lineStyle: { width: w, color }, itemStyle: { color }, _obs: cv.obs_dates });
  const dec = isPrice ? 2 : 3;
  c.setOption({
    animation: false,
    legend: { top: 4, left: 8, icon: "roundRect", itemHeight: 3, itemWidth: 14 },
    tooltip: {
      trigger: "axis", confine: true,
      formatter: (ps) => `<div style="min-width:210px"><b>${ps[0].axisValue}</b><br>${ps.map((p) => {
        const val = p.seriesType === "bar" ? (p.value == null ? "—" : `${p.value >= 0 ? "+" : "−"}${Math.abs(p.value)}${isPrice ? "" : "bp"}`) : (p.value == null ? "미수집" : fmtN(p.value, dec) + (isPrice ? "" : "%"));
        const obs = p.seriesType === "line" ? (p.seriesIndex === 0 ? t0 : p.seriesIndex === 1 ? t1 : t2).obs_dates[p.dataIndex] : null;
        return `${p.marker}${esc(p.seriesName)} <b style="float:right;margin-left:14px">${val}</b>${obs ? `<br><span style="color:#9aa2af;font-size:11px;margin-left:14px">관측일 ${obs}</span>` : ""}`;
      }).join("<br>")}<br><span style="color:#9aa2af;font-size:11px">출처 ${esc(data.src)}</span></div>`,
    },
    grid: [{ left: 56, right: 24, top: 64, height: "46%" }, { left: 56, right: 24, top: "70%", bottom: 28 }],
    xAxis: [{ type: "category", data: data.tenors, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#cfd4dc" } } },
      { type: "category", data: data.tenors, gridIndex: 1, axisLabel: { color: css("--muted") }, axisLine: { lineStyle: { color: "#cfd4dc" } } }],
    yAxis: [{ type: "value", scale: true, gridIndex: 0, name: isPrice ? "가격" : "수익률 %", nameTextStyle: { color: css("--muted"), align: "left" }, axisLabel: { color: css("--muted") }, splitLine: { lineStyle: { color: "#eef0f3" } } },
      { type: "value", gridIndex: 1, name: isPrice ? "변화 (포인트)" : "변화폭 bp", nameTextStyle: { color: css("--muted"), align: "left" }, axisLabel: { color: css("--muted") }, splitLine: { lineStyle: { color: "#eef0f3" } } }],
    series: [
      line(`${base} (기준)`, t0, colors[0], 2.4), line(ui.cmp1, t1, colors[1], 1.6), line(ui.cmp2, t2, colors[2], 1.6),
      { name: `vs ${ui.cmp1}`, type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: diff(t1), itemStyle: { color: colors[1] }, barMaxWidth: 18 },
      { name: `vs ${ui.cmp2}`, type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: diff(t2), itemStyle: { color: colors[2] }, barMaxWidth: 18 },
    ],
  });
}

// ---- 데이터 표 -------------------------------------------------------------
async function tableData() {
  const keys = selection();
  const data = await fetchSeries(keys);
  const ss = keys.map((k) => data[k]).filter((s) => s && s.dates.length).map(sliceRange);
  const dates = [...new Set(ss.flatMap((s) => s.dates))].sort();
  const maps = ss.map((s) => new Map(s.dates.map((d, i) => [d, s.values[i]])));
  const rows = dates.map((d) => [d, ...maps.map((m) => (m.has(d) ? m.get(d) : null))]);
  return { head: ["관측일", ...ss.map((s) => `${s.label}${s.unit === "bp" ? " (bp)" : s.unit === "pct" ? " (%)" : ""}`)], ds: ss.map((s) => s.decimals), rows };
}
async function renderTable(seq) {
  const { head, ds, rows } = await tableData();
  if (seq !== renderSeq) return;
  if (!rows.length) return emptyView("표시할 값이 없습니다 · 타일을 선택하세요");
  if (chart) { chart.dispose(); chart = null; }
  $("view").innerHTML = `<div class="table-scroll"><table><thead><tr>${head.map((h, i) => `<th class="${i ? "" : "txt"}">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.slice().reverse().map((r) =>
    `<tr>${r.map((v, i) => `<td>${i === 0 ? v : v == null ? '<span class="flat">미수집</span>' : fmtN(v, ds[i - 1])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
async function exportCsv() {
  const { head, rows } = await tableData();
  const cell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const text = "﻿" + [head, ...rows].map((r) => r.map(cell).join(",")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  a.download = `marketview_${ui.tab}_${asof || "latest"}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---- 메인 렌더 -------------------------------------------------------------
async function renderView(seq) {
  try {
    if (ui.sub === "ts") await renderTimeSeries(seq);
    else if (ui.sub === "curve") await renderCurve(seq);
    else await renderTable(seq);
  } catch (e) {
    if (seq === renderSeq) emptyView(`불러오지 못했습니다 · ${esc(e.message)} · R 키로 새로고침`);
  }
}
async function renderMain({ skeleton = false, reloadTiles = true } = {}) {
  const seq = ++renderSeq;
  renderTabs();
  if (ui.sub === "curve") renderCurveControls();
  if (skeleton) renderTiles(true);
  const view = renderView(seq);          // 차트·표는 타일 응답과 무관하다 → 타일과 동시에 받는다 (요청이 줄지어 기다리지 않게)
  try {
    if (reloadTiles || !tilesData || tilesData.tab !== ui.tab) {
      const data = await api("/api/tiles?" + qs({ tab: ui.tab, asof }));
      if (seq !== renderSeq) return;
      tilesData = data;
    }
    renderTiles(false);
    renderModeInfo();
  } catch (e) {
    if (seq === renderSeq) { $("tiles").innerHTML = `<div class="empty-note" style="position:static;padding:14px">타일을 불러오지 못했습니다 · ${esc(e.message)}</div>`; }
  }
  await view;
}
function renderModeInfo() {
  const closes = tilesData && tilesData.closes;
  const md = (d) => (d ? d.slice(5).replace("-", ".") : "미수집");
  const pill = $("mode-pill");
  pill.className = "mode-pill " + (asof ? "date" : "close");
  // 지표 검색 왼쪽의 작은 표시: 자세한 설명은 툴팁
  pill.innerHTML = asof ? `<span class="m">기준일</span><b>${asof}</b>`
    : `<span class="m">KR</span><b>${md(closes && closes.kr)}</b> <span class="m">US</span><b>${md(closes && closes.us)}</b>`;
  pill.title = asof ? "상단 기준일에서 고른 날짜까지의 종가·확정값입니다. '오늘'을 누르면 마감 기준으로 돌아갑니다."
    : "마감 기준: 권역(한국·미국)별 마감일의 확정값만 표시합니다. 장중 값은 쓰지 않습니다.";
}
function switchTab(id) {
  if (!meta.tabs.some((t) => t.id === id)) return;
  ui.tab = id; save();
  renderMain({ skeleton: true });
}

// ---- 수동 입력 -------------------------------------------------------------
function openManualInput(key) {
  const t = currentTiles().find((x) => x.key === key);
  if (!t) return;
  const today = meta.today, dflt = meta.input_date;
  popover(`<h3>${esc(t.label)} 입력</h3>
    <p class="hint">소스가 없는 항목입니다. 직접 입력한 값은 이력과 함께 저장되고, 자동 수집이 덮어쓰지 않습니다. 스프레드는 국고채와 같은 관측일끼리만 계산됩니다 (기본값: 국고채 최신 관측일).</p>
    <label>관측일 <input type="date" id="mi-date" value="${dflt}" max="${today}"></label>
    <label>값 <input type="number" id="mi-value" step="any" placeholder="${t.unit === "pct" ? "예: 3.45 (%)" : "숫자"}" style="width:140px"> <span class="lbl">${t.unit === "pct" ? "%" : t.unit === "bp" ? "bp" : ""}</span></label>
    <label>메모 <input type="text" id="mi-note" placeholder="선택" style="flex:1"></label>
    <div style="display:flex;gap:8px;align-items:center;margin-top:6px"><button id="mi-save">저장</button><span id="mi-msg" class="hint"></span></div>
    <details style="margin-top:8px"><summary class="hint" style="cursor:pointer">여러 값을 CSV로 붙여넣기</summary>
      <p class="hint">한 줄에 <code>날짜,항목키,값</code> (예: 2026-10-08,kr_cds_5y,42.5) 또는 첫 줄이 <code>date,항목키,항목키…</code>인 표. 한 줄이라도 틀리면 아무것도 저장하지 않습니다.</p>
      <textarea id="mi-csv" rows="5" style="width:100%" placeholder="date,kr_cds_5y,ndf_1m\n2026-10-08,42.5,1342.5"></textarea>
      <button id="mi-csv-save">CSV 저장</button> <span id="mi-csv-msg" class="hint"></span></details>`, true);
  $("mi-value").focus();
  $("mi-csv-save").onclick = async () => {
    try {
      const r = await send("POST", "/api/observations/csv", { text: $("mi-csv").value });
      $("popover").hidden = true;
      renderMain({ skeleton: false });
      popover(`<h3>CSV 저장됨</h3><p>${r.rows}개 값 (새로 ${r.insert}, 수정 ${r.update}, 변동 없음 ${r.same})</p>`, true);
    } catch (e) { $("mi-csv-msg").textContent = e.message; }
  };
  $("mi-save").onclick = async () => {
    const v = $("mi-value").value;
    if (v === "" || !isFinite(Number(v))) { $("mi-msg").textContent = "값을 숫자로 입력하세요"; return; }
    try {
      await api("/api/observation", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series_key: key, obs_date: $("mi-date").value, value: Number(v), note: $("mi-note").value || null }) });
      $("popover").hidden = true;
      renderMain({ skeleton: false });
    } catch (e) { $("mi-msg").textContent = e.message; }
  };
}

// ---- 뉴스 · 텔레그램 (/api/news) ---------------------------------------------
let newsState = { items: [], keywords: [], telegram: { state: "no_channels", message: "" } };
let newsSeq = 0;
const TAB_LABEL = { bond: "채권", fx: "외환", credit: "신용", equity: "주식" };
function hl(text) {  // 이스케이프한 뒤 키워드를 표시한다 (HTML 을 깨지 않도록 순서가 중요)
  const safe = esc(text);
  if (!newsState.keywords.length) return safe;
  const re = new RegExp("(" + newsState.keywords.map((k) => esc(k).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
  return safe.replace(re, "<mark>$1</mark>");
}
function ago(iso) {
  const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  return m < 1 ? "방금" : m < 60 ? `${m}분 전` : m < 1440 ? `${Math.floor(m / 60)}시간 전` : `${Math.floor(m / 1440)}일 전`;
}
function newsItemHtml(n) {
  const tg = n.kind === "telegram";
  const key = `${n.kind}:${n.id}`;
  const att = n.attachments.map((a) => `<a class="att" href="${esc(safeUrl(n.link))}" target="_blank" rel="noopener" title="텔레그램에서 열기 (${esc(a.name)})">${(() => { const label = a.kind === "pdf" ? "PDF" : a.kind === "photo" ? "이미지" : "파일"; return label + (a.name && a.name !== label ? " · " + esc(a.name) : ""); })()}</a>`).join("");
  const links = n.links.slice(0, 3).map((u) => `<a class="att" href="${esc(safeUrl(u))}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 38))}</a>`).join("");
  const body = tg ? (n.summary || "") : (n.summary || "");
  const searching = $("news-search").value.trim() && n.tab && n.tab !== ui.newsTab ? `<span class="tabtag">${TAB_LABEL[n.tab]}</span>` : "";
  const long = tg && (n.summary || "").length > 160;
  return `<div class="item${n.is_read ? " read" : ""}" data-key="${key}" data-kind="${n.kind}" data-id="${n.id}">
    <div class="top"><span class="src-tag ${tg ? "tg" : ""}">${esc(n.source)}</span><span class="ago" title="${esc(n.published_at)}">${ago(n.published_at)}</span>${n.edited ? '<span class="tabtag" title="채널에서 수정된 메시지입니다 (원본은 DB 에 보관)">수정됨</span>' : ""}${searching}</div>
    <div class="title"><a class="tlink" href="${esc(safeUrl(n.link))}" target="_blank" rel="noopener">${hl(n.title)}</a></div>
    ${body ? `<div class="body">${hl(body)}</div>` : ""}${long ? '<span class="more">더 보기</span>' : ""}
    ${att || links ? `<div class="atts">${att}${links}</div>` : ""}</div>`;
}
async function renderNews() {
  const seq = ++newsSeq;
  $("news-tabs").innerHTML = meta.tabs.map((t) => `<button data-news="${t.id}" class="${t.id === ui.newsTab ? "on" : ""}">${t.label}</button>`).join("");
  const q = $("news-search").value.trim();
  try {
    const data = await api("/api/news?" + qs({ tab: ui.newsTab, q, asof, limit: 80 }));
    if (seq !== newsSeq) return;
    newsState = data;
  } catch (e) {
    if (seq === newsSeq) $("news-list").innerHTML = `<div class="empty-note" style="position:static;padding:24px">뉴스를 불러오지 못했습니다 · ${esc(e.message)}</div>`;
    return;
  }
  const tgBtn = $("tg-state");
  tgBtn.hidden = newsState.telegram.state === "ok";
  tgBtn.textContent = "텔레그램 미연결";
  if (!newsState.items.length) {
    $("news-list").innerHTML = `<div class="empty-note" style="position:static;padding:24px">${q ? "검색 결과 없음 · 키워드를 바꿔 보세요" : asof ? "그날까지 수집된 뉴스가 없습니다" : "수집된 뉴스가 없습니다 · R 키로 새로고침"}</div>`;
    return;
  }
  const list = $("news-list"), top = list.scrollTop;
  const opened = new Set([...list.querySelectorAll(".item.open")].map((el) => el.dataset.key));       // 자동 갱신이 펼쳐 둔 메시지를 접지 않게
  list.innerHTML = newsState.items.map(newsItemHtml).join("");
  for (const el of list.querySelectorAll(".item")) {
    if (!opened.has(el.dataset.key)) continue;
    el.classList.add("open");
    const more = el.querySelector(".more");
    if (more) more.textContent = "접기";
  }
  list.scrollTop = top;
}
function markRead(it) {
  if (!it || it.classList.contains("read")) return;
  it.classList.add("read");
  send("POST", `/api/news/${it.dataset.kind}/${it.dataset.id}/read`, { read: true }).catch(() => it.classList.remove("read"));
}
function telegramHelp() {
  const t = newsState.telegram;
  popover(`<h3>텔레그램 연결</h3><p>${esc(t.message || "")}</p>
    <ol class="hint" style="padding-left:18px;line-height:1.7"><li>https://my.telegram.org 에서 api_id / api_hash 발급 → <code>.env</code> 에 <code>TELEGRAM_API_ID</code>, <code>TELEGRAM_API_HASH</code></li>
    <li><code>.venv\\Scripts\\python -m pip install telethon</code></li>
    <li><code>.venv\\Scripts\\python scripts\\telegram_login.py</code> (전화번호·인증코드는 직접 입력, 채널 목록이 출력됨)</li>
    <li><code>marketview/config/telegram.py</code> 의 CHANNELS 에 읽을 채널을 적고 앱 다시 실행</li></ol>
    <p class="hint">읽기 전용입니다. 세션과 메시지는 이 PC 밖으로 나가지 않습니다.</p>`, true);
}

// ---- 모달 ------------------------------------------------------------------
function openModal(html) { $("modal-card").innerHTML = html; $("modal").hidden = false; }
function closeModal() { $("modal").hidden = true; $("modal-card").innerHTML = ""; }
const val = (id) => $(id).value.trim();
async function send(method, path, body) {
  return api(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
}

// ---- 이벤트 (/api/events) ---------------------------------------------------
let eventsData = [];
const eventBase = () => asof || meta.today;
const OTHER_TZ = ["Asia/Tokyo", "Europe/London", "Europe/Berlin", "UTC"];
async function renderEvents() {
  try {
    eventsData = (await api("/api/events?" + qs({ base: eventBase(), days: 28 }))).events;
  } catch (e) {
    $("events-list").innerHTML = `<div class="empty-note" style="position:static;padding:18px">이벤트를 불러오지 못했습니다 · ${esc(e.message)}</div>`;
    return;
  }
  if (!eventsData.length) {
    $("events-list").innerHTML = '<div class="empty-note" style="position:static;padding:18px">향후 4주 일정 없음 · "+ 추가"로 직접 입력하세요</div>';
    return;
  }
  const origin = { official: "공식", fred: "FRED", manual: "수동" };
  $("events-list").innerHTML = eventsData.map((e) => {
    const cls = e.d_day === 0 ? " today" : e.d_day === 1 ? " tomorrow" : "";
    const md = e.kst_date.slice(5).replace("-", "/");
    const when = e.time_known ? `${md}(${e.weekday}) ${e.kst_time} KST` : `${md}(${e.weekday}) 시각 미정`;
    const local = e.tz !== "Asia/Seoul" && e.time_known ? ` · 현지 ${e.local_date.slice(5).replace("-", "/")} ${e.local_time}` : "";
    const extra = [e.consensus && `컨센서스 ${e.consensus}`, e.memo].filter(Boolean).join(" · ");
    return `<div class="ev${cls}" data-id="${e.id}" title="클릭해서 컨센서스·메모를 적거나 수정">
      <div class="dd">${e.d_day === 0 ? "D-day" : "D-" + e.d_day}</div>
      <div><div class="t">${esc(e.title)}</div><div class="sub">${when}${local}</div>${extra ? `<div class="memo">${esc(extra)}</div>` : ""}</div>
      <div class="badges"><span class="src" title="출처 확인일 ${e.verified_on || "-"}">${origin[e.origin]}</span><span class="imp" title="중요도">${"●".repeat(e.importance)}</span><span class="cc">${e.region}</span></div></div>`;
  }).join("");
}
function openEventForm(ev) {
  const isNew = !ev, locked = ev && ev.origin !== "manual";
  const e = ev || { title: "", region: "KR", importance: 2, local_date: eventBase(), local_time: "", tz: "Asia/Seoul", consensus: "", memo: "", source_url: "" };
  const dis = locked ? "disabled" : "";
  const regionOpts = ["KR", "US", "OTHER"].map((r) => `<option ${r === e.region ? "selected" : ""}>${r}</option>`).join("");
  const tzOpts = OTHER_TZ.map((t) => `<option ${t === e.tz ? "selected" : ""}>${t}</option>`).join("");
  openModal(`<h3>${isNew ? "이벤트 추가" : esc(e.title)}</h3>
    ${locked ? `<p class="hint">${e.origin === "official" ? "공식 일정" : "FRED 발표일"}입니다. 날짜·시각·제목은 출처가 정하므로 컨센서스·메모·중요도만 고칠 수 있습니다.${e.source_url ? ` <a href="${esc(safeUrl(e.source_url))}" target="_blank" rel="noopener">출처</a>` : ""} (확인일 ${e.verified_on || "-"})</p>` : ""}
    <div class="form-grid">
      <label>제목</label><input id="ef-title" value="${esc(e.title)}" ${dis}>
      <label>지역</label><span class="two"><select id="ef-region" ${dis}>${regionOpts}</select><select id="ef-tz" ${dis} ${e.region === "OTHER" ? "" : "hidden"}>${tzOpts}</select></span>
      <label>현지 날짜</label><input type="date" id="ef-date" value="${e.local_date}" ${dis}>
      <label>현지 시각</label><span class="two"><input type="time" id="ef-time" value="${e.local_time || ""}" ${dis}><span class="unit">비우면 '시각 미정'</span></span>
      <label>중요도</label><select id="ef-imp">${[1, 2, 3].map((n) => `<option value="${n}" ${n === e.importance ? "selected" : ""}>${"●".repeat(n)}</option>`).join("")}</select>
      <label>컨센서스</label><input id="ef-cons" value="${esc(e.consensus || "")}" placeholder="예: 전월비 +0.3%">
      <label>메모</label><textarea id="ef-memo" placeholder="시나리오, 확인할 것">${esc(e.memo || "")}</textarea>
    </div>
    <p class="hint" id="ef-kst"></p>
    <div class="modal-actions"><button id="ef-save">저장</button><button id="ef-cancel">취소</button>
      ${isNew ? "" : '<button id="ef-del" class="danger">삭제</button>'}<span class="msg" id="ef-msg"></span></div>`);
  const upd = () => {
    const r = $("ef-region").value;
    $("ef-tz").hidden = r !== "OTHER";
    const t = $("ef-time").value, d = $("ef-date").value;
    const tz = r === "KR" ? "Asia/Seoul" : r === "US" ? "America/New_York" : $("ef-tz").value;
    if (!d) { $("ef-kst").textContent = ""; return; }
    if (!t) { $("ef-kst").textContent = "시각 미정으로 저장됩니다 (날짜만 표시)."; return; }
    const kst = new Date(new Date(localAsUtc(d, t, tz)));
    const p = parts("Asia/Seoul", kst);
    $("ef-kst").textContent = `→ KST ${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} (서머타임 반영)`;
  };
  ["ef-region", "ef-tz", "ef-date", "ef-time"].forEach((id) => $(id).addEventListener("input", upd));
  upd();
  $("ef-cancel").onclick = closeModal;
  if ($("ef-del")) $("ef-del").onclick = async () => {
    if (!confirm("이 이벤트를 삭제할까요? (이력은 남습니다)")) return;
    try { await send("DELETE", `/api/events/${e.id}`); closeModal(); renderEvents(); } catch (x) { $("ef-msg").textContent = x.message; }
  };
  $("ef-save").onclick = async () => {
    const body = locked
      ? { importance: Number($("ef-imp").value), consensus: val("ef-cons") || null, memo: val("ef-memo") || null }
      : { title: val("ef-title"), region: $("ef-region").value, tz: $("ef-region").value === "OTHER" ? $("ef-tz").value : null,
          local_date: $("ef-date").value, local_time: $("ef-time").value || null, importance: Number($("ef-imp").value),
          consensus: val("ef-cons") || null, memo: val("ef-memo") || null, kind: e.kind || "other" };
    try {
      await send(isNew ? "POST" : "PUT", isNew ? "/api/events" : `/api/events/${e.id}`, body);
      closeModal(); renderEvents();
    } catch (x) { $("ef-msg").textContent = x.message; }
  };
}
function localAsUtc(isoDate, hhmm, tz) {  // 미리보기용: 현지 시각 → 절대 시각(ms). 서버가 저장 시 같은 규칙으로 계산한다
  const [y, m, d] = isoDate.split("-").map(Number), [hh, mm] = hhmm.split(":").map(Number);
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  const off = (ms) => { const q = parts(tz, new Date(ms)); return Date.UTC(+q.year, +q.month - 1, +q.day, +q.hour, +q.minute) - Math.floor(ms / 60000) * 60000; };
  let t = guess - off(guess);
  t = guess - off(t);
  return t;
}

// ---- 포지션 (/api/positions) -------------------------------------------------
let positionsData = [], trackables = null;
const unitSfx = (u) => (u === "pct" ? "%" : u === "bp" ? "bp" : u === "krw" ? "원" : "");
const fmtLevel = (v, p) => (v == null ? "—" : fmtN(v, p.decimals) + unitSfx(p.unit));
// userdata.side_hint 와 같은 규칙: 금리(%)는 채권 관행(롱 = 금리 하락 시 이익), 그 밖은 레벨 기준
const sideHint = (side, unit) => {
  const up = (side === "long") !== (unit === "pct");
  return `${side === "long" ? "롱" : "숏"} — ${unit === "pct" ? "금리" : "레벨"} ${up ? "상승" : "하락"} 시 이익`;
};
async function renderPositions() {
  try {
    positionsData = (await api("/api/positions?" + qs({ closed: $("pos-closed").checked ? "true" : "" }))).positions;
  } catch (e) {
    $("pos-table").innerHTML = `<tbody><tr><td class="txt">포지션을 불러오지 못했습니다 · ${esc(e.message)}</td></tr></tbody>`;
    return;
  }
  const head = ["상품", "방향", "진입 레벨", "진입일", "목표", "손절", "근거", "현재", "손익"];
  const rows = positionsData.map((p) => {
    const closed = p.status === "closed";
    const cur = closed ? fmtLevel(p.exit_level, p) : p.current != null ? fmtLevel(p.current, p) : '<span class="flat">미수집</span>';
    const pnlCls = p.pnl == null ? "flat" : dirClass(p.pnl);
    const pnl = p.pnl == null ? "—" : `${p.pnl > 0 ? "+" : p.pnl < 0 ? "−" : ""}${fmtN(Math.abs(p.pnl), p.pnl_unit === "pt" ? 2 : 1)}${p.pnl_unit}`;
    const tip = p.series_key ? "" : ' title="연결된 시세가 없어 손익을 자동 계산하지 않습니다"';
    return `<tr class="pos${closed ? " closed" : ""}" data-id="${p.id}"><td class="txt">${esc(p.product)}${closed ? ' <span class="src">청산</span>' : ""}</td>
      <td title="${esc(p.side_hint || "")}">${p.side === "long" ? "롱" : "숏"}</td><td>${fmtLevel(p.entry_level, p)}</td><td>${p.entry_date.slice(5)}</td>
      <td>${fmtLevel(p.target, p)}</td><td>${fmtLevel(p.stop, p)}</td><td class="txt">${esc(p.rationale || "")}</td>
      <td title="${p.current_obs ? "관측일 " + p.current_obs : ""}">${cur}</td><td class="${pnlCls}"${tip}>${pnl}${closed && p.pnl != null ? ' <span class="hint">확정</span>' : ""}</td></tr>`;
  }).join("");
  $("pos-table").innerHTML = `<colgroup><col style="width:15%"><col style="width:4%"><col style="width:10%"><col style="width:6%"><col style="width:10%"><col style="width:10%"><col style="width:21%"><col style="width:12%"><col style="width:12%"></colgroup>
    <thead><tr>${head.map((h, i) => `<th class="${i === 0 || i === 6 ? "txt" : ""}">${h}</th>`).join("")}</tr></thead>
    <tbody>${rows || '<tr><td class="txt" colspan="9"><span class="flat">열린 포지션 없음 · 아래 "+ 포지션 추가"로 입력하세요</span></td></tr>'}
    <tr><td class="txt add-row" colspan="9" id="pos-add">+ 포지션 추가</td></tr></tbody>`;
}
async function openPositionForm(p) {
  if (!trackables) { try { trackables = (await api("/api/trackables")).items; } catch { trackables = []; } }
  const isNew = !p;
  const e = p || { product: "", series_key: "", side: "long", entry_level: "", entry_date: meta.input_date, target: "", stop: "", rationale: "", exit_level: "", exit_date: "" };
  const opts = trackables.map((t) => `<option value="${t.key}" ${t.key === e.series_key ? "selected" : ""}>${esc(t.label)}</option>`).join("");
  const num = (v) => (v == null ? "" : v);
  openModal(`<h3>${isNew ? "포지션 추가" : "포지션 수정"}</h3>
    <div class="form-grid">
      <label>연결 시세</label><select id="pf-key"><option value="">없음 (손익 자동 계산 안 함)</option>${opts}</select>
      <label>상품</label><input id="pf-product" value="${esc(e.product)}" placeholder="예: 국고 3−10 스티프너">
      <label>방향</label><select id="pf-side"><option value="long" ${e.side === "long" ? "selected" : ""}></option><option value="short" ${e.side === "short" ? "selected" : ""}></option></select>
      <label>진입 레벨</label><span class="two"><input type="number" step="any" id="pf-entry" value="${num(e.entry_level)}"><span class="unit" id="pf-unit"></span></span>
      <label>진입일</label><input type="date" id="pf-edate" value="${e.entry_date}">
      <label>목표</label><input type="number" step="any" id="pf-target" value="${num(e.target)}">
      <label>손절</label><input type="number" step="any" id="pf-stop" value="${num(e.stop)}">
      <label>근거 한 줄</label><input id="pf-why" value="${esc(e.rationale || "")}">
    </div>
    <div class="sect">청산 (선택 — 레벨과 날짜를 둘 다 입력하면 청산 처리)</div>
    <div class="form-grid"><label>청산 레벨</label><input type="number" step="any" id="pf-xlevel" value="${num(e.exit_level)}"><label>청산일</label><input type="date" id="pf-xdate" value="${e.exit_date || ""}"></div>
    <div class="modal-actions"><button id="pf-save">저장</button><button id="pf-cancel">취소</button>${isNew ? "" : '<button id="pf-del" class="danger">삭제</button>'}<span class="msg" id="pf-msg"></span></div>`);
  const unitHint = () => {
    const t = trackables.find((x) => x.key === $("pf-key").value);
    $("pf-unit").textContent = t ? `${unitSfx(t.unit) || "레벨"} 단위로 입력` : "";
    for (const o of $("pf-side").options) o.textContent = sideHint(o.value, t && t.unit);      // 연결 시세가 금리(%)면 금리 기준 문구
  };
  $("pf-key").onchange = () => {
    const t = trackables.find((x) => x.key === $("pf-key").value);
    if (t && !$("pf-product").value.trim()) $("pf-product").value = t.label;
    unitHint();
  };
  unitHint();
  $("pf-cancel").onclick = closeModal;
  if ($("pf-del")) $("pf-del").onclick = async () => {
    if (!confirm("이 포지션을 삭제할까요? (이력은 남습니다)")) return;
    try { await send("DELETE", `/api/positions/${p.id}`); closeModal(); renderPositions(); } catch (x) { $("pf-msg").textContent = x.message; }
  };
  $("pf-save").onclick = async () => {
    const body = { product: val("pf-product"), series_key: $("pf-key").value || null, side: $("pf-side").value,
      entry_level: $("pf-entry").value, entry_date: $("pf-edate").value, target: $("pf-target").value || null, stop: $("pf-stop").value || null,
      rationale: val("pf-why") || null, exit_level: $("pf-xlevel").value || null, exit_date: $("pf-xdate").value || null };
    try {
      const res = await send(isNew ? "POST" : "PUT", isNew ? "/api/positions" : `/api/positions/${p.id}`, body);
      closeModal(); renderPositions();
      if (res && res.warnings && res.warnings.length) alert("저장했습니다. 확인하세요:\n" + res.warnings.join("\n"));
    } catch (x) { $("pf-msg").textContent = x.message; }
  };
}

function setupUserPanels() {
  $("ev-add").onclick = () => openEventForm(null);
  $("events-list").onclick = (e) => { const el = e.target.closest(".ev"); if (el) openEventForm(eventsData.find((x) => x.id === Number(el.dataset.id))); };
  $("pos-closed").onchange = renderPositions;
  $("pos-add-btn").onclick = () => openPositionForm(null);
  $("pos-table").onclick = (e) => {
    if (e.target.closest("#pos-add")) { openPositionForm(null); return; }
    const tr = e.target.closest("tr.pos");
    if (tr) openPositionForm(positionsData.find((x) => x.id === Number(tr.dataset.id)));
  };
  $("modal").addEventListener("mousedown", (e) => { if (e.target === $("modal")) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("modal").hidden) closeModal(); });
}

// ---- 지표 검색 (메인 패널 오른쪽 위) ---------------------------------------
let findSeq = 0, findItems = [], findIdx = -1, findTimer;
const findUnit = (u) => (u === "pct" ? "%" : u === "bp" ? " bp" : u === "krw" ? " 원" : "");
function paintFind() {
  const box = $("data-results");
  box.innerHTML = findItems.map((it, i) => {
    const v = it.state ? `${fmtN(it.state.value, it.decimals)}${findUnit(it.unit)}` : (it.manual ? "수동 입력" : "미수집");
    const when = it.state ? ` · ${it.state.obs_date.slice(5)} ${esc(it.state.src)}` : "";
    const where = it.shown ? `${esc(it.tab_label)}${it.group_label ? " · " + esc(it.group_label) : ""}` : `${esc(it.tab_label)} · <span class="off">화면 타일에 없음 (저장된 지표) · 차트로 열기</span>`;
    return `<button class="res${i === findIdx ? " on" : ""}" data-i="${i}"><span class="n">${esc(it.label)}</span>
      <span class="v${it.state ? "" : " na"}">${v}</span><span class="w">${where}${when}</span></button>`;
  }).join("");
}
async function runFind() {
  const q = $("data-search").value.trim(), box = $("data-results");
  if (!q) { box.hidden = true; findItems = []; return; }
  const seq = ++findSeq;
  try {
    const r = await api("/api/find?" + qs({ q, asof }));
    if (seq !== findSeq) return;
    findItems = r.items; findIdx = r.items.length ? 0 : -1;
    if (r.items.length) paintFind(); else box.innerHTML = '<div class="none">검색 결과 없음 · 지표 이름이나 키로 찾습니다 (예: 10년, UST, 국고 10-3)</div>';
  } catch (e) {
    if (seq !== findSeq) return;
    findItems = []; box.innerHTML = `<div class="none">검색하지 못했습니다 · ${esc(e.message)}</div>`;
  }
  box.hidden = false;
}
function pickFind(i) {
  const it = findItems[i];
  if (!it) return;
  ui.tab = it.tab; ui.sel[it.tab] = [it.key];
  if (it.group) ui.group[it.tab] = it.group;               // 화면에 없는 지표는 그룹을 바꾸지 않고 차트에만 올린다
  if (ui.sub === "curve") ui.sub = "ts";
  save();
  $("data-search").value = ""; $("data-results").hidden = true; findItems = [];
  closeFind();
  renderMain({ skeleton: true });
}
function openFind() {
  $("data-search-btn").hidden = true;
  $("data-search").hidden = false;
  $("data-search").focus();
}
function closeFind() {                                      // 입력이 비어 있을 때만 돋보기 아이콘으로 접는다
  const input = $("data-search");
  if (input.value.trim()) return;
  $("data-results").hidden = true;
  input.hidden = true;
  $("data-search-btn").hidden = false;
}
function setupFind() {
  const input = $("data-search"), box = $("data-results");
  $("data-search-btn").onclick = (e) => { e.stopPropagation(); openFind(); };
  input.onblur = () => setTimeout(() => { if (document.activeElement !== input) closeFind(); }, 150);
  input.oninput = () => { clearTimeout(findTimer); findTimer = setTimeout(runFind, 150); };
  input.onfocus = () => { if (input.value.trim() && findItems.length) box.hidden = false; };
  input.onkeydown = (e) => {
    if (e.key === "Escape") { input.value = ""; box.hidden = true; closeFind(); return; }
    if (box.hidden || !findItems.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      findIdx = (findIdx + (e.key === "ArrowDown" ? 1 : -1) + findItems.length) % findItems.length;
      paintFind();
      const on = box.querySelector(".res.on"); if (on) on.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") { e.preventDefault(); pickFind(findIdx); }
  };
  box.onmousedown = (e) => { const b = e.target.closest("[data-i]"); if (b) { e.preventDefault(); pickFind(Number(b.dataset.i)); } };
  document.addEventListener("click", (e) => { if (!e.target.closest(".dsearch")) { box.hidden = true; closeFind(); } });
}

// ---- 상단 바 · 상태 · 팝오버 ----------------------------------------------
let knownVer = null;      // /api/status 의 versions (패널별 데이터 버전)
function fmtRefresh(iso) {
  if (!iso) return "—";
  const t = iso.slice(11, 19), d = iso.slice(0, 10);
  return d === kstYmd(new Date()) ? t : `${d.slice(5)} ${t}`;
}
function popover(html, keep = false) {
  const p = $("popover");
  if (!html || (!keep && !p.hidden && p.dataset.html === html)) { p.hidden = true; p.dataset.html = ""; return; }
  p.innerHTML = html; p.dataset.html = html; p.hidden = false;
}
let lastFailures = [], lastReport = null, lastBackup = {};
const driveFailedReport = () => lastReport && lastReport.drive_status === "failed";
const driveFailedBackups = () => Object.entries(lastBackup).filter(([, v]) => v.drive === "failed");
function reportResult(r) {
  const state = (s) => ({ ok: "완료", failed: "실패", pending: "대기", skipped: "건너뜀" }[s] || s);
  const links = [`<a href="/api/report/file?id=${r.id}&kind=md" target="_blank">Markdown</a>`,
    r.has_pdf ? `<a href="/api/report/file?id=${r.id}&kind=pdf" target="_blank">PDF</a>` : ""].filter(Boolean).join(" · ");
  const how = { auto: "자동 생성", catchup: "따라잡기(꺼져 있던 날)" }[r.origin] || "";
  return `<div class="row"><b>${esc(r.name)}${how ? ` <span class="flat">· ${how}</span>` : ""}</b><span>${links}</span></div>
    <div class="row"><span class="flat">PDF</span><span>${state(r.pdf_status)}</span></div>
    <div class="row"><span class="flat">Google Drive 복사</span><span>${state(r.drive_status)}</span></div>
    ${r.message ? `<div class="hint" style="padding:4px 0">${esc(r.message)}</div>` : ""}
    <p class="hint">로컬 reports 폴더에는 항상 저장됩니다. 같은 날짜를 다시 만들면 -v2, -v3 …으로 저장됩니다.</p>`;
}
async function openReportPanel() {
  let info = { run: null, telegram: true };
  try { info = await api("/api/report/last"); } catch { /* 서버 응답 없음이면 패널은 기본값으로 */ }
  const day = asof || meta.today;
  popover(`<h3>일일 리포트</h3>
    <label class="asof"><span class="lbl">기준일</span><input type="date" id="rep-date" value="${day}" max="${meta.today}"></label>
    <label><input type="checkbox" id="rep-tg" ${info.telegram ? "checked" : ""}> 텔레그램 리서치 포함</label>
    <label><input type="checkbox" id="rep-auto" ${info.auto ? "checked" : ""}> 자동 생성 (평일 ${esc(info.auto_time || "07:30")} 이후, 꺼져 있던 평일은 다음 실행 때 따라잡기)</label>
    ${info.auto_last && info.auto_last.errors && info.auto_last.errors.length ? `<div class="hint">자동 생성 실패: ${info.auto_last.errors.map((e) => esc(e.date + " " + e.message)).join(" / ")}</div>` : ""}
    <div style="margin:8px 0"><button id="rep-go">리포트 생성</button> <span class="hint" id="rep-msg"></span></div>
    <div id="rep-last">${info.run ? `<div class="hint">최근 생성</div>${reportResult(info.run)}` : '<p class="hint">아직 생성한 리포트가 없습니다.</p>'}</div>`, true);
  $("rep-tg").onchange = () => api("/api/report/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ telegram: $("rep-tg").checked }) }).catch((e) => ($("rep-msg").textContent = e.message));
  $("rep-auto").onchange = () => api("/api/report/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto: $("rep-auto").checked }) }).catch((e) => ($("rep-msg").textContent = e.message));
  $("rep-go").onclick = async () => {
    const btn = $("rep-go"), d = $("rep-date").value;
    btn.disabled = true; $("rep-msg").textContent = "만드는 중… (차트·PDF 변환에 10~30초)";
    try {
      const r = await api(`/api/report?date=${encodeURIComponent(d)}`, { method: "POST" });
      $("rep-msg").textContent = "";
      $("rep-last").innerHTML = `<div class="hint">방금 생성</div>${reportResult(r.run)}`;
    } catch (e) { $("rep-msg").textContent = e.message; }
    btn.disabled = false;
    refreshStatus();
  };
}
async function refreshStatus() {
  try {
    const st = await api("/api/status");
    $("last-refresh").textContent = fmtRefresh(st.last_refresh);
    const c = st.cloud;
    $("cloud-sync").textContent = st.cloud_sync ? fmtRefresh(st.cloud_sync)
      : (c && c.state === "ok" ? "수집 대기" : (c && !["no_remote", "no_branch"].includes(c.state) ? "오류" : "미연결"));
    $("cloud-sync").title = !c ? "클라우드 수집기(GitHub Actions)는 아직 연결되지 않았습니다"
      : c.state !== "ok" ? (c.message || "")
      : `마지막 회차: ${c.mode || "-"} · ${c.status || "-"} · 확인 ${fmtRefresh(c.checked_at)}${c.added ? ` · 이번에 합친 값 ${c.added.observations}, 뉴스 ${c.added.news}` : ""}`;
    lastFailures = st.failures || [];
    const warn = $("warn-btn");
    warn.hidden = !lastFailures.length;
    warn.textContent = `수집 경고 ${lastFailures.length}`;
    $("refresh-btn").textContent = st.collecting ? "수집 중…" : "새로고침";
    $("refresh-btn").title = "새로고침 (R)";
    if (st.collecting) fastPollSoon();             // 끝난 것을 30초 주기 확인까지 기다리지 않는다
    lastReport = st.report;
    lastBackup = st.backup || {};
    $("drive-btn").hidden = !(driveFailedReport() || driveFailedBackups().length);
    return st;
  } catch {
    // 서버가 꺼지면 마지막 상태('수집 중…')가 그대로 남아 계속 수집하는 것처럼 보였다 → 끊김을 분명히 알린다
    $("last-refresh").textContent = "서버 응답 없음";
    const rb = $("refresh-btn");
    rb.textContent = "서버 꺼짐";
    rb.title = "서버가 응답하지 않습니다. 바탕화면 MarketView 아이콘으로 다시 실행하세요 (켜지면 자동으로 복구됩니다)";
    return null;
  }
}
let fastPollTimer = null;
function fastPollSoon() {
  if (fastPollTimer) return;
  fastPollTimer = setTimeout(async () => { fastPollTimer = null; await pollStatus(); }, 1500);
}
// 상태를 확인하고, 수집이 새 값을 가져온 패널만 조용히 새로 그린다 (과거 기준일을 볼 때는 건드리지 않는다).
// 버전(versions)은 시세·뉴스·이벤트별로 따로 오른다 → 15분마다 도는 뉴스 회차가 차트를 다시 만들어 확대 상태를 풀지 않는다
async function pollStatus() {
  const s = await refreshStatus();
  if (!s || !s.versions || $("refresh-btn").dataset.busy) return;
  const old = knownVer;
  knownVer = s.versions;
  for (const el of document.querySelectorAll("#news-list .ago")) el.textContent = ago(el.title);   // 새 기사가 없어도 'N분 전' 은 흐른다
  if (asof || !old) return;
  if (s.versions.obs !== old.obs) { renderMain({ skeleton: false }); renderPositions(); }
  if (s.versions.news !== old.news) renderNews();
  if (s.versions.events !== old.events) renderEvents();
}
async function refresh() {
  if ($("refresh-btn").dataset.busy) return;
  $("refresh-btn").dataset.busy = "1";
  renderTiles(true);
  try {
    await api("/api/refresh", { method: "POST" });
    for (let i = 0; i < 100; i++) {          // 최대 ~150초 기다린다. 화면은 그동안 스켈레톤
      await new Promise((r) => setTimeout(r, 1500));
      const st = await refreshStatus();
      if (st && !st.collecting) break;
    }
  } catch (e) { popover(`<h3>새로고침 실패</h3><p>${esc(e.message)}</p>`); }
  delete $("refresh-btn").dataset.busy;
  const st = await refreshStatus();
  if (st) knownVer = st.versions;                  // 아래에서 전부 다시 그리므로 다음 확인 때 또 그리지 않는다
  renderMain({ skeleton: false });
  renderEvents(); renderPositions(); renderNews();
}
const applyColors = () => { document.documentElement.dataset.colors = ui.colors; };
function applyLayout() {
  const r = document.documentElement.style;
  r.setProperty("--left-w", ui.cols + "%");
  r.setProperty("--views-h", ui.views + "%");
  r.setProperty("--news-h", ui.events + "%");
}
function resizeChart() { if (chart) chart.resize(); }
function setupTopbar() {
  $("warn-btn").onclick = (e) => {
    e.stopPropagation();
    const by = {};
    for (const f of lastFailures) (by[f.source] ||= []).push(f);
    popover(`<h3>수집 실패 항목</h3>${Object.entries(by).map(([src, fs]) => `<div class="row"><b>${esc(src)}</b><span class="flat">${fs.map((f) => esc(f.series_key)).join(", ")}</span></div><div class="hint" style="padding:2px 0 6px">${esc(fs[0].message || "")}</div>`).join("")}<p class="hint">나머지 소스는 정상 수집되었습니다. 실패한 항목은 직전 값을 그대로 보여줍니다.</p>`);
  };
  $("refresh-btn").onclick = refresh;
  $("report-btn").onclick = (e) => { e.stopPropagation(); openReportPanel(); };
  $("drive-btn").onclick = (e) => {
    e.stopPropagation();
    const rep = driveFailedReport() ? `<h3>리포트</h3>${reportResult({ ...lastReport, name: lastReport.report_date + (lastReport.version > 1 ? "-v" + lastReport.version : ""), has_pdf: lastReport.pdf_status === "ok" })}` : "";
    const bak = driveFailedBackups().map(([k, v]) => `<h3>${k === "daily" ? "DB 백업" : "주간 CSV"} (${esc(v.date)})</h3><div class="hint">${esc(v.message || "")}</div>`).join("");
    popover(`<h3>Google Drive 복사 실패</h3>${rep}${bak}
      <p class="hint">로컬 파일은 이미 저장되어 있습니다. Drive 앱이 로그인·마운트된 뒤 리포트는 다시 생성(-v2), 백업은 15분 안에 자동으로 다시 시도합니다.</p>`);
  };
  $("settings-btn").onclick = (e) => {
    e.stopPropagation();
    popover(`<h3>설정</h3><label><input type="radio" name="col" value="kr" ${ui.colors === "kr" ? "checked" : ""}> 상승 빨강 / 하락 파랑 (한국식)</label>
      <label><input type="radio" name="col" value="us" ${ui.colors === "us" ? "checked" : ""}> 상승 초록 / 하락 빨강</label>
      <p class="hint">금리 상승에 좋다/나쁘다 의미를 담지 않고 방향만 나타냅니다.</p><button id="reset-layout">레이아웃 비율 초기화</button>`, true);
    document.querySelectorAll('input[name="col"]').forEach((r) => (r.onchange = () => { ui.colors = r.value; applyColors(); save(); renderMain({ reloadTiles: false }); }));
    $("reset-layout").onclick = () => { Object.assign(ui, { cols: 70, views: 18, events: 60 }); applyLayout(); save(); resizeChart(); };
  };
  document.addEventListener("click", (e) => { if (!$("popover").contains(e.target)) $("popover").hidden = true; });
  $("asof").max = meta.today;
  $("asof").onchange = () => { asof = $("asof").value; ui.cmp1 = ui.cmp2 = ""; renderMain({ skeleton: true }); renderEvents(); renderNews(); };
  $("asof-clear").onclick = () => { asof = ""; $("asof").value = ""; ui.cmp1 = ui.cmp2 = ""; renderMain({ skeleton: true }); renderEvents(); renderNews(); };
}
function setupSplitters() {
  document.querySelectorAll(".splitter").forEach((sp) => {
    sp.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      sp.setPointerCapture(ev.pointerId);
      sp.classList.add("drag");
      const body = $("body").getBoundingClientRect(), left = $("left").getBoundingClientRect(), right = $("right").getBoundingClientRect();
      const move = (e) => {
        const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
        if (sp.dataset.split === "cols") ui.cols = clamp(((e.clientX - body.left) / body.width) * 100, 50, 85);
        else if (sp.dataset.split === "views") ui.views = clamp(((left.bottom - e.clientY) / left.height) * 100, 10, 40);
        else ui.events = clamp(((e.clientY - right.top) / right.height) * 100, 30, 80);
        applyLayout(); resizeChart();
      };
      const up = () => { sp.classList.remove("drag"); sp.removeEventListener("pointermove", move); sp.removeEventListener("pointerup", up); save(); };
      sp.addEventListener("pointermove", move);
      sp.addEventListener("pointerup", up);
    });
  });
}

// ---- 이벤트 바인딩 ---------------------------------------------------------
function bind() {
  $("main-tabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) switchTab(b.dataset.tab); };
  $("group-chips").onclick = (e) => {
    const b = e.target.closest("[data-group]");
    if (b) { ui.group[ui.tab] = b.dataset.group; save(); renderTabs(); renderTiles(); renderModeInfo(); }
  };
  $("sub-tabs").onclick = (e) => { const b = e.target.closest("[data-sub]"); if (b && !b.disabled) { ui.sub = b.dataset.sub; save(); renderMain({ reloadTiles: false }); } };
  $("ranges").onclick = (e) => { const b = e.target.closest("[data-range]"); if (b) { ui.range = b.dataset.range; save(); renderTabs(); renderMain({ reloadTiles: false }); } };
  $("tiles").onclick = (e) => {
    const inp = e.target.closest("[data-input]");
    if (inp) { e.stopPropagation(); openManualInput(inp.dataset.input); return; }
    const b = e.target.closest("[data-k]");
    if (!b) return;
    const k = b.dataset.k, t = currentTiles().find((x) => x.key === k);
    if (t && t.manual && !t.state) { e.stopPropagation(); openManualInput(k); return; }
    const cur = selection();
    ui.sel[ui.tab] = e.ctrlKey || e.metaKey || e.shiftKey ? (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k].slice(-6)) : [k];
    if (!ui.sel[ui.tab].length) ui.sel[ui.tab] = [k];
    if (ui.sub === "curve") ui.sub = "ts";
    save(); renderMain({ reloadTiles: false });
  };
  $("curve-controls").addEventListener("change", (e) => {
    if (e.target.id === "family") ui.family = e.target.value;
    if (e.target.id === "cmp1") ui.cmp1 = e.target.value;
    if (e.target.id === "cmp2") ui.cmp2 = e.target.value;
    save(); renderMain({ reloadTiles: false });
  });
  $("csv-btn").onclick = exportCsv;
  $("news-tabs").onclick = (e) => { const b = e.target.closest("[data-news]"); if (b) { ui.newsTab = b.dataset.news; save(); renderNews(); } };
  let newsTimer;
  $("news-search").oninput = () => { clearTimeout(newsTimer); newsTimer = setTimeout(renderNews, 250); };
  $("tg-state").onclick = (e) => { e.stopPropagation(); telegramHelp(); };
  $("news-list").onclick = (e) => {
    const it = e.target.closest(".item");
    if (!it) return;
    if (e.target.closest("a")) { markRead(it); return; }          // 원문 링크: 기본 동작(새 창)을 그대로 두고 읽음만 표시
    it.classList.toggle("open");
    const more = it.querySelector(".more");
    if (more) more.textContent = it.classList.contains("open") ? "접기" : "더 보기";
    markRead(it);
  };
  document.addEventListener("keydown", (e) => {
    if (!$("modal").hidden || e.target.matches("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;   // 모달이 열려 있으면 뒤 화면을 건드리지 않는다
    const idx = "1234".indexOf(e.key);
    if (idx >= 0 && meta.tabs[idx]) switchTab(meta.tabs[idx].id);
    else if (e.key === "r" || e.key === "R") refresh();
    else if (e.key === "/") { e.preventDefault(); openFind(); }
  });
  new ResizeObserver(resizeChart).observe($("view"));
}

// ---- 시작 ------------------------------------------------------------------
async function boot() {
  applyColors();
  applyLayout();
  tickClock();
  setInterval(tickClock, 1000);
  try {
    meta = await api("/api/meta");
  } catch (e) {
    $("view").innerHTML = `<div class="empty-note">서버에 연결하지 못했습니다 · ${esc(e.message)}<br>scripts\\start.bat 으로 다시 실행하세요</div>`;
    return;
  }
  // 이전 버전이 저장한 화면 상태가 지금 형식과 다를 수 있다 (예: group 이 문자열이던 시절) → 형식이 틀리면 초기화
  if (!ui.group || typeof ui.group !== "object") ui.group = {};
  if (!ui.sel || typeof ui.sel !== "object") ui.sel = {};
  for (const [tab, keys] of Object.entries(ui.sel)) if (!Array.isArray(keys)) delete ui.sel[tab];
  delete ui.read;                                   // 쓰이지 않는 옛 값 정리
  if (!meta.tabs.some((t) => t.id === ui.tab)) ui.tab = meta.tabs[0].id;
  // 정의에서 빠진 지표(USD 스왑, 신용 섹터 등)가 저장된 선택에 남아 있으면 걸러낸다. 타일에 없는 저장 지표(검색으로 연 것)는 유지한다.
  const known = new Set(meta.known_keys);
  for (const t of meta.tabs) {
    if (!ui.sel[t.id]) continue;
    ui.sel[t.id] = ui.sel[t.id].filter((k) => known.has(k));
    if (!ui.sel[t.id].length) delete ui.sel[t.id];
  }
  delete ui.tenor;
  delete ui.mode;                                   // 마감/장중 전환은 없앴다(2026-10-14): 저장된 값 정리
  if (!meta.curves.some((c) => c.id === ui.family)) ui.family = meta.curves[0].id;
  // 주소 쿼리로 화면 상태를 지정할 수 있다 (예: /?tab=bond&group=ktbf&sub=curve&family=ktb&range=3M). 저장하지 않는다.
  const q = new URLSearchParams(location.search);
  if (q.get("tab") && meta.tabs.some((t) => t.id === q.get("tab"))) ui.tab = q.get("tab");
  if (q.get("group")) ui.group = { ...ui.group, [ui.tab]: q.get("group") };
  if (["ts", "curve", "table"].includes(q.get("sub"))) ui.sub = q.get("sub");
  if (q.get("family") && meta.curves.some((c) => c.id === q.get("family"))) ui.family = q.get("family");
  if (q.get("range") && ["1M", "3M", "6M", "1Y", "3Y", "전체"].includes(q.get("range"))) ui.range = q.get("range");
  if (q.get("keys")) ui.sel = { ...ui.sel, [ui.tab]: q.get("keys").split(",") };
  if (/^\d{4}-\d{2}-\d{2}$/.test(q.get("asof") || "")) { asof = q.get("asof"); }
  setupTopbar();
  if (asof) $("asof").value = asof;
  setupFind();
  setupSplitters();
  bind();
  renderNews();
  renderEvents();
  renderPositions();
  setupUserPanels();
  const st = await refreshStatus();
  knownVer = st && st.versions;
  await renderMain({ skeleton: true });
  // 앱이 켜져 있는 동안 백그라운드 수집이 끝나면 화면을 조용히 새로 그린다 (과거 기준일을 볼 때는 건드리지 않는다)
  setInterval(pollStatus, 30000);
}
boot();
window.__mv = { ui, kstYmd, parts, get meta() { return meta; } };  // 검증용
