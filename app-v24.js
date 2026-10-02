const state = {
  data: null,
  shownMonth: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  selectedDate: new Date()
};

const $ = (id) => document.getElementById(id);
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const monthKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
const jpDate = (d) => new Intl.DateTimeFormat("ja-JP", { month: "long", day: "numeric", weekday: "short" }).format(d);
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const routeData = () => state.data?.routes?.hainan || {};

function getDayInfo(date) {
  const route = routeData();
  const key = monthKey(date);
  const month = route.months?.[key] || (route.coverage_month === key ? route : null);
  const info = month?.overrides?.[iso(date)];
  if (info) return { ...info, confirmed: true };
  return {
    status: "unknown",
    note: month
      ? "この日の運行予定はPDFから確認できません。公式PDFをご確認ください。"
      : `${date.getMonth() + 1}月の変更案内はまだ取得できていません。通常ダイヤを参考表示しています。`,
    confirmed: false
  };
}

function normalizedStatus(info) {
  if (["no_service", "none"].includes(info?.status)) return "none";
  if (["changed", "modified"].includes(info?.status)) return "changed";
  if (info?.status === "special" && info.mode) return `mode-${String(info.mode).toLowerCase()}`;
  if (info?.status === "normal") return "normal";
  return "unknown";
}

function statusLabel(info) {
  if (["no_service", "none"].includes(info?.status)) return "運休";
  if (["changed", "modified"].includes(info?.status)) return info.mode ? `${info.mode}便（変更あり）` : "変更あり";
  if (info?.mode) return `${info.mode}便`;
  if (info?.status === "normal") return "通常";
  return "未確認";
}

function getSchoolDepartures(info) {
  const base = routeData().base || {};
  if (info.mode === "D") return base.saturday_no_school?.from_school || [];
  if (["B", "C"].includes(info.mode)) return base.saturday_school?.from_school || [];
  return base.weekday?.from_school || [];
}

function renderHero(date) {
  const info = getDayInfo(date);
  const status = normalizedStatus(info);
  $("heroDate").textContent = jpDate(date);
  const heroTitle = $("heroTitle");
  const mode = String(info.mode || "").toUpperCase();
  heroTitle.textContent = mode
    ? `${mode}便`
    : status === "none" ? "運休"
    : status === "normal" ? "通常運行"
    : status === "changed" ? "変更あり"
    : "未確認";
  heroTitle.className = `hero-mode ${mode ? `mode-${mode.toLowerCase()}` : status}`;

  const pill = $("heroStatus");
  pill.className = `status-pill changed${status === "changed" && mode ? "" : " hidden"}`;
  pill.textContent = status === "changed" && mode ? "変更あり" : "";
  $("heroDetails").textContent = info.note || "";

  const month = routeData().months?.[monthKey(date)] || routeData();
  const link = $("sourceLink");
  const url = info.source_url || month.source_url || routeData().source_url;
  if (url) {
    link.href = url;
    link.classList.remove("hidden");
  } else {
    link.removeAttribute("href");
    link.classList.add("hidden");
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function scheduleSection(title, items) {
  return `<div class="schedule-block"><p class="schedule-heading">${escapeHtml(title)}</p><div class="time-chips">${items.map((item, i) =>
    `<div class="time-chip"><span class="chip-label">${escapeHtml(item.label || `${i + 1}便`)}</span><strong class="chip-time">${escapeHtml(item.school_departure || item.time || "—")}</strong></div>`
  ).join("")}</div></div>`;
}

function renderSchedule(date) {
  const info = getDayInfo(date);
  const status = normalizedStatus(info);
  const box = $("scheduleTable");
  if (status === "none") {
    box.innerHTML = '<div class="empty-state">この日のスクールバスは運休です。</div>';
    return;
  }
  if (status === "changed") {
    if (Array.isArray(info.departures) && info.departures.length) {
      box.innerHTML = scheduleSection("変更後の学校出発時刻", info.departures.map((time, i) => ({ label: `${i + 1}便`, time })));
    } else {
      box.innerHTML = '<div class="empty-state">時刻に変更があります。公式PDFで変更後の時刻をご確認ください。</div>';
    }
    return;
  }

  const trips = getSchoolDepartures(info);
  if (!trips.length) {
    box.innerHTML = '<div class="empty-state">学校出発時刻を確認できません。公式PDFをご確認ください。</div>';
    return;
  }
  let html = scheduleSection("下校 · 学校出発時刻", trips);
  if (status === "unknown") html += '<div class="empty-state" style="padding-top:14px">変更案内が未確認のため、通常ダイヤを参考表示しています。</div>';
  box.innerHTML = html;
}

function renderCalendar() {
  const month = state.shownMonth;
  const year = month.getFullYear();
  const monthIndex = month.getMonth();
  $("monthLabel").textContent = `${year}年${monthIndex + 1}月`;
  const first = new Date(year, monthIndex, 1);
  const mondayOffset = (first.getDay() + 6) % 7;
  const start = addDays(first, -mondayOffset);
  const calendar = $("calendar");
  calendar.replaceChildren();

  for (let i = 0; i < 42; i++) {
    const date = addDays(start, i);
    const info = getDayInfo(date);
    const status = normalizedStatus(info);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "day";
    if (date.getMonth() !== monthIndex) button.classList.add("outside");
    if (iso(date) === iso(new Date())) button.classList.add("today");
    if (iso(date) === iso(state.selectedDate)) button.classList.add("selected");
    if (status === "changed") button.classList.add("has-change");
    const modeClass = info.mode ? `mode-${String(info.mode).toLowerCase()}` : status;
    button.setAttribute("aria-label", `${jpDate(date)}、${statusLabel(info)}`);
    button.setAttribute("aria-pressed", String(iso(date) === iso(state.selectedDate)));
    button.innerHTML = `<span class="num">${date.getDate()}</span><span class="mini-status ${modeClass}"></span>`;
    button.addEventListener("click", () => {
      state.selectedDate = date;
      state.shownMonth = new Date(date.getFullYear(), date.getMonth(), 1);
      renderAll();
    });
    calendar.append(button);
  }
}

function renderMeta() {
  const currentInfo = getDayInfo(new Date());
  const notice = $("dataNotice");
  if (currentInfo.status === "unknown") {
    notice.className = "notice";
    notice.textContent = currentInfo.note;
  } else {
    notice.className = "notice hidden";
    notice.textContent = "";
  }
}

function renderAll() {
  renderCalendar();
  renderHero(state.selectedDate);
  renderSchedule(state.selectedDate);
  renderMeta();
}

async function boot() {
  try {
    const bundledData = window.SCHOOL_BUS_SCHEDULE;
    if (location.protocol === "file:") {
      if (!bundledData) throw new Error("Offline schedule data is missing");
      state.data = bundledData;
    } else {
      try {
        const response = await fetch(`./data/schedule.json?v=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        state.data = await response.json();
      } catch (error) {
        if (!bundledData) throw error;
        state.data = bundledData;
      }
    }
    renderAll();
  } catch (error) {
    const notice = $("dataNotice");
    notice.className = "notice error";
    notice.textContent = "データを読み込めませんでした。再読み込みするか、公式サイトを確認してください。";
    console.error(error);
  }
}

$("prevMonth").addEventListener("click", () => {
  state.shownMonth = new Date(state.shownMonth.getFullYear(), state.shownMonth.getMonth() - 1, 1);
  renderCalendar();
});
$("nextMonth").addEventListener("click", () => {
  state.shownMonth = new Date(state.shownMonth.getFullYear(), state.shownMonth.getMonth() + 1, 1);
  renderCalendar();
});

boot();
