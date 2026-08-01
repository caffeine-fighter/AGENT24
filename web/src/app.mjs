import {
  PHASES,
  createCakeCrashFixture,
  createInitialState,
  reduceRunState,
} from "./core.mjs";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const configuredApiBase = new URLSearchParams(window.location.search).get("api");
const apiBase = configuredApiBase ? new URL(configuredApiBase, window.location.href) : new URL(window.location.origin);

function apiUrl(path) {
  return new URL(path, apiBase).href;
}

let state = createInitialState();
let timers = [];
let eventSource = null;
let runStartedAt = null;
let clockTimer = null;
let lastMission = state.mission;
let receivedLiveEvent = false;

function setText(selector, value) {
  const element = $(selector);
  if (element) element.textContent = value;
}

function renderWorld(prefix, world) {
  setText(`#${prefix}Wallet`, won.format(world.wallet_krw));
  setText(`#${prefix}Orders`, String(world.orders));
  setText(`#${prefix}Emails`, String(world.outbound_emails));
  setText(`#${prefix}Calendar`, String(world.calendar_events));
  setText(`#${prefix}Files`, String(world.files_touched));
}

function renderVerdict(selector, verdict, labels) {
  const element = $(selector);
  element.className = `verdict ${verdict}`;
  element.textContent = labels[verdict] || labels.neutral;
}

function renderPhases() {
  $$("#phaseRail li").forEach((item) => {
    const phase = item.dataset.phase;
    item.classList.toggle("active", phase === state.phase && state.status !== "complete");
    item.classList.toggle("done", state.completedPhases.includes(phase) || state.status === "complete");
  });
}

function renderAssets() {
  renderWorld("before", state.before);
  renderWorld("after", state.after);
  const beforeCard = $('[data-world="before"]');
  beforeCard.querySelector(".wallet").classList.toggle("damaged", state.beforeVerdict === "fail");
  beforeCard.querySelector(".orders").classList.toggle("damaged", state.beforeVerdict === "fail");
  const afterCard = $('[data-world="after"]');
  afterCard.querySelectorAll(".asset-card").forEach((card) => card.classList.toggle("safe", state.afterVerdict === "pass"));
  renderVerdict("#beforeVerdict", state.beforeVerdict, { neutral: "대기 중", fail: "CRASHED", pass: "SURVIVED" });
  renderVerdict("#afterVerdict", state.afterVerdict, { neutral: "검증 전", fail: "FAILED", pass: "SURVIVED" });

  setText("#impactLabel", state.impact.label);
  setText("#impactHeadline", state.impact.headline);
  setText("#impactDetail", state.impact.detail);
  const cakeStack = $("#cakeStack");
  const cakeCount = Math.min(state.before.orders, 12);
  cakeStack.setAttribute("aria-label", `주문된 케이크 ${state.before.orders}개`);
  cakeStack.innerHTML = Array.from({ length: cakeCount }, (_, index) => `<span style="animation-delay:${index * 45}ms">🎂</span>`).join("");
}

function renderAutopsy() {
  const timeline = $("#autopsyTimeline");
  if (!state.autopsy.length) {
    timeline.innerHTML = '<li class="placeholder">실행 후 최초로 잘못된 tool call까지 되감습니다.</li>';
    setText("#evidenceCount", "0 evidence");
    return;
  }
  timeline.innerHTML = state.autopsy
    .map((step) => `<li class="${step.kind === "divergence" ? "divergence" : ""}">${escapeHtml(step.text)}</li>`)
    .join("");
  setText("#evidenceCount", `${state.autopsy.length} evidence`);
}

function renderPatch() {
  setText("#patchStatus", state.patch ? "적용 및 재검증" : "아직 생성되지 않음");
  setText("#patchPreview", state.patch || "payment.charge:\n  policy: waiting_for_autopsy");
}

function renderChecks() {
  let hasValue = false;
  let allPass = true;
  Object.entries(state.checks).forEach(([key, value]) => {
    const row = $(`[data-check="${key}"]`);
    if (!row) return;
    row.classList.toggle("pass", value === true);
    row.classList.toggle("fail", value === false);
    row.querySelector("strong").textContent = value === null ? "—" : value ? "PASS" : "FAIL";
    if (value !== null) hasValue = true;
    if (value !== true) allPass = false;
  });
  const label = state.status === "complete"
    ? allPass ? "검증 완료" : hasValue ? "검증 종료" : "검증 데이터 없음"
    : hasValue ? "검증 중" : "대기 중";
  setText("#verificationStatus", label);
}

function renderStream() {
  const stream = $("#rawStream");
  if (!state.events.length) {
    stream.innerHTML = '<div class="stream-empty">실행을 시작하면 tool_call / tool_result가 이곳에 표시됩니다.</div>';
    setText("#eventCount", "0 EVENTS");
    return;
  }

  const fragment = document.createDocumentFragment();
  state.events.forEach((event) => {
    const node = $("#streamLineTemplate").content.firstElementChild.cloneNode(true);
    node.dataset.kind = event.type;
    node.querySelector(".stream-seq").textContent = `#${String(event.seq).padStart(2, "0")}`;
    node.querySelector(".stream-type").textContent = event.wire_type || event.type;
    node.querySelector("time").textContent = new Date(event.timestamp).toLocaleTimeString("ko-KR", { hour12: false });
    node.querySelector("pre").textContent = JSON.stringify(event.raw, null, 2);
    fragment.appendChild(node);
  });
  stream.replaceChildren(fragment);
  stream.scrollTop = stream.scrollHeight;
  setText("#eventCount", `${state.events.length} EVENTS`);
}

function render() {
  document.body.dataset.running = String(state.status === "running");
  renderPhases();
  renderAssets();
  renderAutopsy();
  renderPatch();
  renderChecks();
  renderStream();
  setText("#runId", state.runId ? `RUN ${state.runId}` : "RUN —");
  const liveMode = state.mode === "offline_demo" ? "API / OFFLINE FALLBACK" : "LIVE API";
  setText("#modeBadge", state.source === "live" ? liveMode : "AUTO / FIXTURE");
  setText("#connectionStatus", state.source === "live" ? "SSE CONNECTED" : "SYNTHETIC WORLD ONLY");
  $("#runButton").disabled = state.status === "running";
  $("#replayButton").disabled = state.status !== "complete";
}

function dispatch(event) {
  state = reduceRunState(state, event);
  render();
  if (state.source === "live" && ["complete", "failed"].includes(state.status)) {
    eventSource?.close();
    eventSource = null;
    clearInterval(clockTimer);
    const terminalMode = state.mode === "offline_demo" ? "OFFLINE FALLBACK" : "LIVE API";
    setText("#connectionStatus", state.status === "complete" ? `${terminalMode} · COMPLETE` : `${terminalMode} · FAILED`);
  }
}

function clearRun() {
  timers.forEach(clearTimeout);
  timers = [];
  eventSource?.close();
  eventSource = null;
  clearInterval(clockTimer);
  clockTimer = null;
  runStartedAt = null;
}

function startClock() {
  runStartedAt = Date.now();
  clearInterval(clockTimer);
  const update = () => {
    const elapsed = Math.floor((Date.now() - runStartedAt) / 1000);
    setText("#runClock", `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`);
  };
  update();
  clockTimer = setInterval(update, 1000);
}

function playFixture(mission, { speed = 360 } = {}) {
  clearRun();
  state = createInitialState();
  state.mission = mission;
  lastMission = mission;
  render();
  startClock();
  const events = createCakeCrashFixture(mission);
  events.forEach((fixtureEvent, index) => {
    timers.push(setTimeout(() => {
      dispatch(fixtureEvent);
      if (fixtureEvent.type === "run.completed") clearInterval(clockTimer);
    }, index * speed));
  });
}

async function startLiveRun(mission) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1600);
  try {
    const response = await fetch(apiUrl("/api/runs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: mission }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Run API returned ${response.status}`);
    const payload = await response.json();
    const runId = payload.run_id;
    if (!runId) throw new Error("Run API response has no run_id");
    clearTimeout(timeout);
    clearRun();
    state = createInitialState();
    state.source = "live";
    state.mode = payload.mode || "live";
    state.mission = mission;
    lastMission = mission;
    receivedLiveEvent = false;
    render();
    startClock();
    const eventsPath = payload.events_url || `/api/runs/${encodeURIComponent(runId)}/events`;
    eventSource = new EventSource(apiUrl(eventsPath));
    eventSource.onmessage = ({ data }) => {
      receivedLiveEvent = true;
      try { dispatch({ ...JSON.parse(data), source: "live" }); }
      catch (error) { dispatch({ run_id: runId, type: "client.parse_error", source: "live", data: { message: error.message }, raw: data }); }
    };
    eventSource.onerror = () => {
      eventSource?.close();
      if (!receivedLiveEvent) playFixture(mission);
      else setText("#connectionStatus", "SSE CLOSED · EVENTS PRESERVED");
    };
    return true;
  } catch {
    clearTimeout(timeout);
    return false;
  }
}

async function runMission(mission) {
  const live = await startLiveRun(mission);
  if (!live) playFixture(mission);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

$("#missionForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const mission = $("#missionInput").value.trim();
  if (mission) runMission(mission);
});

$("#resetButton").addEventListener("click", () => {
  clearRun();
  state = createInitialState();
  $("#missionInput").value = lastMission;
  setText("#runClock", "00:00");
  render();
});

$("#replayButton").addEventListener("click", () => playFixture(lastMission, { speed: 270 }));

$("#copyStreamButton").addEventListener("click", async () => {
  const raw = state.events.map((event) => JSON.stringify(event.raw)).join("\n");
  try {
    await navigator.clipboard.writeText(raw);
    setText("#copyStreamButton", "COPIED");
    setTimeout(() => setText("#copyStreamButton", "COPY"), 1200);
  } catch {
    setText("#copyStreamButton", "COPY FAILED");
  }
});

render();
