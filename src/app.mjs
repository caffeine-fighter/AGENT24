import {
  createCakeCrashFixture,
  createInitialState,
  formatRunInput,
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
let lastTarget = { ...state.target };
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
    item.classList.toggle("done", state.completedPhases.includes(phase));
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

function compactJson(value) {
  return JSON.stringify(value, null, 2);
}

function renderLabReport() {
  const panel = $("#labReportPanel");
  const report = state.reportView;
  const behavior = state.behaviorProfileView;
  const experiment = state.experimentPlanView;
  const source = state.sourceDescriptorView;
  panel.hidden = !report && !behavior && !experiment;
  if (!report && !behavior && !experiment) return;

  const profile = behavior || report?.profile || {
    agentName: "Agent 정보 없음",
    capabilities: [],
    tools: [],
    sideEffectTools: [],
    permissions: {},
  };
  const capabilityText = profile.capabilities.length
    ? profile.capabilities
        .map((capability) => `${capability.tool} [${capability.categories.join(", ") || "unknown"}]`)
        .join(" · ")
    : profile.tools?.join(" · ") || profile.sideEffectTools?.join(" · ") || "관찰된 capability 없음";
  const permissions = Object.keys(profile.permissions).length
    ? compactJson(profile.permissions)
    : "명시된 permission 없음";
  const termination = report?.experiment.termination;
  const observedItems = (report?.observed.items || [])
    .map((item) => `${item.invariant}: ${compactJson(item.actual)} / ${item.expected} (${item.evidence})`)
    .join("\n");

  setText("#reportAgent", profile.agentName);
  setText(
    "#reportSourceRef",
    behavior
      ? `${source?.sourceRef || behavior.sourceRef} · ${source ? source.resolver.toUpperCase() : behavior.baselineObserved ? "BASELINE OBSERVED" : "NO BASELINE"}`
      : source?.sourceRef || "LabReport agent card",
  );
  setText("#reportCapabilities", capabilityText);
  setText(
    "#reportBudget",
    report
      ? `${report.experiment.runs} runs · ${report.experiment.costUnits} cost`
      : `${behavior.missionFamily} · PROFILE READY`,
  );
  setText("#reportPermissions", permissions);
  setText(
    "#reportTermination",
    termination?.reason || (experiment ? "experiment_plan" : behavior ? "behavior_profile" : "termination 정보 없음"),
  );

  const experimentCard = $("#selectedExperimentCard");
  experimentCard.hidden = !experiment;
  if (experiment) {
    const fault = experiment.faults[0];
    setText(
      "#reportExperiment",
      fault ? `${fault.kind.toUpperCase()} → ${fault.targetTool}` : "지원 가능한 fault 없음",
    );
    setText(
      "#reportExperimentMeta",
      `${experiment.scenarioId} · seed ${experiment.seed ?? "?"} · ${experiment.maxTurns ?? "?"} turns · ${experiment.singleVariable ? "SINGLE VARIABLE" : "MULTI VARIABLE"}`,
    );
    setText("#reportExperimentReason", `WHY · ${experiment.toolChoiceReason}`);
    setText("#reportExperimentEvidence", `EXPECT · ${experiment.expectedEvidence}`);
  }

  const assessmentGrid = $("#behaviorAssessments");
  assessmentGrid.hidden = !behavior;
  if (behavior) {
    const fragment = document.createDocumentFragment();
    behavior.assessments.forEach((assessment) => {
      const card = document.createElement("article");
      card.dataset.verdict = assessment.value;
      const header = document.createElement("div");
      const name = document.createElement("strong");
      const verdict = document.createElement("span");
      const detail = document.createElement("p");
      name.textContent = assessment.name.replaceAll("_", " ");
      verdict.textContent = assessment.value.toUpperCase();
      detail.textContent = assessment.evidence.length
        ? assessment.evidence.join("\n")
        : assessment.unknownReason || "evidence 없음";
      header.append(name, verdict);
      card.append(header, detail);
      fragment.appendChild(card);
    });
    assessmentGrid.replaceChildren(fragment);
  }

  const claimGrid = $("#claimGrid");
  const residualRisk = $("#reportResidualRisk");
  claimGrid.hidden = !report;
  residualRisk.hidden = !report;
  if (!report) return;
  setText(
    "#reportObserved",
    observedItems ? `${report.observed.headline}\n${observedItems}` : report.observed.headline,
  );
  setText(
    "#reportHypothesis",
    report.hypothesis
      ? `${report.hypothesis.category} · ${report.hypothesis.statement}`
      : "증거가 없어 진단 가설을 확정하지 않았습니다.",
  );
  setText(
    "#reportProposed",
    report.proposedPatch ? compactJson(report.proposedPatch) : "검증할 패치가 제안되지 않았습니다.",
  );
  setText(
    "#reportVerified",
    report.verification
      ? `${report.verification.accepted ? "ACCEPTED" : "REJECTED"} · ${report.verification.passedGates}/${report.verification.totalGates} gates pass`
      : "protected replay로 검증되지 않았습니다.",
  );
  const limits = [...report.residualRisk, ...report.unsupportedScope];
  setText(
    "#reportResidualRisk",
    limits.length ? `RESIDUAL / UNSUPPORTED · ${limits.join(" · ")}` : "RESIDUAL RISK · 보고된 잔여 위험 없음",
  );
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
  renderLabReport();
  renderStream();
  setText("#targetRepo", state.target.repositoryUrl || "입력 대기");
  setText("#targetRef", state.target.requestedRef || "입력 대기");
  setText(
    "#targetSha",
    state.target.resolvedSha || (state.source === "fixture" && state.status !== "idle"
      ? "FIXTURE · source 미조회"
      : "실행 이벤트 대기"),
  );
  const notice = $("#runNotice");
  notice.hidden = !state.terminalNotice;
  notice.dataset.kind = state.terminalNotice?.kind || "";
  notice.textContent = state.terminalNotice?.message || "";
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

function playFixture(target, { speed = 360 } = {}) {
  clearRun();
  state = createInitialState(target);
  lastTarget = { ...target };
  render();
  startClock();
  const events = createCakeCrashFixture(target.mission, target);
  events.forEach((fixtureEvent, index) => {
    timers.push(setTimeout(() => {
      dispatch(fixtureEvent);
      if (fixtureEvent.type === "run.completed") clearInterval(clockTimer);
    }, index * speed));
  });
}

async function startLiveRun(target) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1600);
  try {
    const response = await fetch(apiUrl("/api/runs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input: formatRunInput(target),
        target: {
          repository_url: target.repositoryUrl,
          requested_ref: target.requestedRef || null,
          mission: target.mission,
        },
      }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Run API returned ${response.status}`);
    const payload = await response.json();
    const runId = payload.run_id;
    if (!runId) throw new Error("Run API response has no run_id");
    clearTimeout(timeout);
    clearRun();
    state = createInitialState(target);
    state.source = "live";
    state.mode = payload.mode || "live";
    lastTarget = { ...target };
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
      if (!receivedLiveEvent) playFixture(target);
      else setText("#connectionStatus", "SSE CLOSED · EVENTS PRESERVED");
    };
    return true;
  } catch {
    clearTimeout(timeout);
    return false;
  }
}

async function runMission(target) {
  const live = await startLiveRun(target);
  if (!live) playFixture(target);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

$("#missionForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const target = {
    repositoryUrl: $("#repositoryInput").value.trim(),
    requestedRef: $("#refInput").value.trim(),
    resolvedSha: null,
    mission: $("#missionInput").value.trim(),
  };
  if (target.repositoryUrl && target.requestedRef && target.mission) runMission(target);
});

$("#resetButton").addEventListener("click", () => {
  clearRun();
  state = createInitialState(lastTarget);
  $("#repositoryInput").value = lastTarget.repositoryUrl;
  $("#refInput").value = lastTarget.requestedRef;
  $("#missionInput").value = lastTarget.mission;
  setText("#runClock", "00:00");
  render();
});

$("#replayButton").addEventListener("click", () => playFixture(lastTarget, { speed: 270 }));

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
