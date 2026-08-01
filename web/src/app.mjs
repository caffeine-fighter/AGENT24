import {
  createCakeCrashFixture,
  createInitialState,
  formatRunInput,
  reduceRunState,
  validateTargetInput,
} from "./core.mjs";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const OUTCOME_STATUS_LABELS = Object.freeze({
  accepted: "요청 접수",
  budget_exhausted: "실험 한도 도달",
  complete: "완료",
  failed: "실패",
  fallback: "데모로 전환",
  fallback_complete: "데모 완료",
  fixture_only: "예시만 실행",
  measured: "문제 확인",
  no_failure_observed: "문제 미발견",
  not_analyzed: "확인 안 함",
  not_run: "실행 안 함",
  pending: "대기 중",
  pinned: "소스 확인",
  profiling: "정보 확인 중",
  ready: "실행 대기",
  running: "실행 중",
  unsupported: "지원하지 않음",
});
const ASSESSMENT_LABELS = Object.freeze({
  retry_behavior: "재시도 방식",
  idempotency_usage: "중복 실행 방지",
  reconciliation_usage: "결제 상태 확인",
  untrusted_input_handling: "외부 입력 처리",
  loop_budget: "반복 실행 한도",
});
const ASSESSMENT_VALUE_LABELS = Object.freeze({
  absent: "없음",
  present: "있음",
  unknown: "확인 안 됨",
});
const CATEGORY_LABELS = Object.freeze({
  privileged_sink: "권한이 필요한 작업",
  side_effect: "외부 상태 변경",
  untrusted_source: "신뢰할 수 없는 입력",
});
const INVARIANT_LABELS = Object.freeze({
  "platform.exactly_once_payment": "결제 중복 방지",
  "task.max_spend_krw": "결제 한도",
  "task.purchase_count": "주문 횟수",
  "task.total_spend": "총 결제 금액",
});
const MISSION_FAMILY_LABELS = Object.freeze({ purchase: "구매 작업" });
const DIAGNOSIS_LABELS = Object.freeze({ duplicate_side_effect: "같은 작업이 두 번 실행됨" });
const RESOLVER_LABELS = Object.freeze({
  fixture: "내장 예시",
  "github-api": "GitHub에서 확인",
  "github-http-404": "저장소를 찾지 못함",
  "github-unavailable": "GitHub 연결 실패",
});
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
  renderVerdict("#beforeVerdict", state.beforeVerdict, { neutral: "대기 중", fail: "문제 재현", pass: "통과" });
  renderVerdict("#afterVerdict", state.afterVerdict, { neutral: "검증 전", fail: "실패", pass: "통과" });

  setText("#impactLabel", state.impact.label);
  setText("#impactHeadline", state.impact.headline);
  setText("#impactDetail", state.impact.detail);
  const cakeStack = $("#cakeStack");
  const cakeCount = Math.min(state.before.orders, 12);
  cakeStack.setAttribute("aria-label", `처리된 케이크 ${state.before.orders}개`);
  cakeStack.innerHTML = Array.from({ length: cakeCount }, (_, index) => `<span style="animation-delay:${index * 45}ms">🎂</span>`).join("");
}

function renderAutopsy() {
  const timeline = $("#autopsyTimeline");
  if (!state.autopsy.length) {
    timeline.innerHTML = '<li class="placeholder">실행을 시작하면 처음 잘못된 도구 호출까지 거슬러 올라갑니다.</li>';
    setText("#evidenceCount", "근거 0개");
    return;
  }
  timeline.innerHTML = state.autopsy
    .map((step) => `<li class="${step.kind === "divergence" ? "divergence" : ""}">${escapeHtml(step.text)}</li>`)
    .join("");
  setText("#evidenceCount", `근거 ${state.autopsy.length}개`);
}

function renderPatch() {
  setText("#patchStatus", state.patch ? "적용 후 다시 검증" : "제안 대기");
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
    row.querySelector("strong").textContent = value === null ? "—" : value ? "통과" : "실패";
    if (value !== null) hasValue = true;
    if (value !== true) allPass = false;
  });
  const label = state.status === "complete"
    ? allPass ? "검증 완료" : hasValue ? "검증 종료" : "검증 데이터 없음"
    : hasValue ? "검증 중" : "대기 중";
  setText("#verificationStatus", label);
}

function renderOutcomes() {
  for (const axis of ["submission", "investigation", "operation"]) {
    const outcome = state.outcomes[axis];
    const card = $(`[data-outcome="${axis}"]`);
    if (!outcome || !card) continue;
    card.dataset.status = outcome.status;
    setText(`#${axis}Outcome`, OUTCOME_STATUS_LABELS[outcome.status] || outcome.status);
    setText(`#${axis}Detail`, outcome.message);
  }
  const scopeNotice = $("#scopeNotice");
  scopeNotice.dataset.scope = state.analysisScope;
  scopeNotice.textContent = state.analysisScope === "fixture_fallback"
    ? "내장 예시로 전환했습니다. 제출한 저장소를 분석한 결과가 아닙니다. 가상 환경의 원본 기록만 보여드리며, 이 결과로 안전을 보장할 수 없습니다."
    : "가상 환경에서만 실행합니다. 외부 저장소의 코드는 실행하지 않습니다. 확인된 저장소 정보와 허용된 설정 파일을 바탕으로 행동을 재현하며, 문제를 찾지 못했더라도 안전하다고 단정할 수 없습니다.";
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
    agentName: "에이전트 정보 없음",
    capabilities: [],
    tools: [],
    sideEffectTools: [],
    permissions: {},
  };
  const capabilityText = profile.capabilities.length
    ? profile.capabilities
        .map((capability) => `${capability.tool} [${capability.categories.map((category) => CATEGORY_LABELS[category] || category).join(", ") || "분류 없음"}]`)
        .join(" · ")
    : profile.tools?.join(" · ") || profile.sideEffectTools?.join(" · ") || "확인된 기능 없음";
  const permissions = Object.keys(profile.permissions).length
    ? Object.entries(profile.permissions)
        .map(([key, value]) => key === "max_spend_krw" ? `최대 결제 금액 ${won.format(value)}` : `${key}: ${value}`)
        .join(" · ")
    : "설정된 권한 없음";
  const termination = report?.experiment.termination;
  const sourceStatus = source
    ? RESOLVER_LABELS[source.resolver] || source.resolver
    : behavior?.baselineObserved
      ? "기본 동작 확인"
      : "기본 동작 미확인";
  const observedItems = (report?.observed.items || [])
    .map((item) => `${INVARIANT_LABELS[item.invariant] || item.invariant}: 실제 ${compactJson(item.actual)} / 기준 ${item.expected} (${item.evidence})`)
    .join("\n");

  setText("#reportAgent", profile.agentName);
  setText(
    "#reportSourceRef",
    behavior
      ? `${source?.sourceRef || behavior.sourceRef} · ${sourceStatus}`
      : source?.sourceRef || "보고서의 에이전트 정보",
  );
  setText("#reportCapabilities", capabilityText);
  setText(
    "#reportBudget",
    report
      ? `실행 ${report.experiment.runs}회 · 비용 ${report.experiment.costUnits}`
      : `${MISSION_FAMILY_LABELS[behavior.missionFamily] || behavior.missionFamily} · 정보 확인 완료`,
  );
  setText("#reportPermissions", permissions);
  setText(
    "#reportTermination",
    termination?.reason === "coverage_complete"
      ? "검증 완료"
      : experiment
        ? "실험 계획 준비"
        : behavior
          ? "에이전트 정보 확인"
          : "종료 상태 정보 없음",
  );

  const experimentCard = $("#selectedExperimentCard");
  experimentCard.hidden = !experiment;
  if (experiment) {
    const fault = experiment.faults[0];
    setText(
      "#reportExperiment",
      fault ? `${fault.kind === "commit_then_timeout" ? "결제 완료 뒤 응답 끊김" : fault.kind} → ${fault.targetTool}` : "실행할 수 있는 오류 유형 없음",
    );
    setText(
      "#reportExperimentMeta",
      `${experiment.scenarioId} · 시드 ${experiment.seed ?? "?"} · 최대 ${experiment.maxTurns ?? "?"}회 · ${experiment.singleVariable ? "오류 1개만 변경" : "오류 여러 개 변경"}`,
    );
    setText("#reportExperimentReason", `선택 근거 · ${experiment.toolChoiceReason}`);
    setText("#reportExperimentEvidence", `확인할 기록 · ${experiment.expectedEvidence}`);
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
      name.textContent = ASSESSMENT_LABELS[assessment.name] || assessment.name.replaceAll("_", " ");
      verdict.textContent = ASSESSMENT_VALUE_LABELS[assessment.value] || assessment.value;
      detail.textContent = assessment.evidence.length
        ? assessment.evidence.join("\n")
        : assessment.unknownReason || "확인할 근거 없음";
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
      ? `${DIAGNOSIS_LABELS[report.hypothesis.category] || report.hypothesis.category} · ${report.hypothesis.statement}`
      : "근거가 부족해 원인을 확정하지 않았습니다.",
  );
  setText(
    "#reportProposed",
    report.proposedPatch ? compactJson(report.proposedPatch) : "검증할 보호책이 아직 제안되지 않았습니다.",
  );
  setText(
    "#reportVerified",
    report.verification
      ? `${report.verification.accepted ? "검증 통과" : "검증 실패"} · 확인 항목 ${report.verification.passedGates}/${report.verification.totalGates}개 통과`
      : "보호책을 적용한 재실행 결과가 없습니다.",
  );
  const limits = [...report.residualRisk, ...report.unsupportedScope];
  setText(
    "#reportResidualRisk",
    limits.length ? `아직 확인하지 못한 위험 · ${limits.join(" · ")}` : "현재 보고된 잔여 위험 없음",
  );
}

function renderStream() {
  const stream = $("#rawStream");
  if (!state.events.length) {
    stream.innerHTML = '<div class="stream-empty">실험을 시작하면 도구 호출 기록이 여기에 표시됩니다.</div>';
    setText("#eventCount", "이벤트 0개");
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
  setText("#eventCount", `이벤트 ${state.events.length}개`);
}

function render() {
  document.body.dataset.running = String(state.status === "running");
  renderPhases();
  renderAssets();
  renderAutopsy();
  renderPatch();
  renderChecks();
  renderOutcomes();
  renderLabReport();
  renderStream();
  setText("#targetRepo", state.target.repositoryUrl || "입력 대기");
  setText("#targetRef", state.target.requestedRef || "입력 대기");
  setText(
    "#targetSha",
    state.target.resolvedSha || (state.source === "fixture" && state.status !== "idle"
      ? "내장 예시 · 저장소 확인 안 함"
      : "실행 대기"),
  );
  const notice = $("#runNotice");
  notice.hidden = !state.terminalNotice;
  notice.dataset.kind = state.terminalNotice?.kind || "";
  notice.textContent = state.terminalNotice?.message || "";
  setText("#runId", state.runId ? `실행 ID ${state.runId}` : "실행 ID —");
  const liveMode = state.mode === "offline_demo" ? "OpenAI 설명 없이 실행" : "실시간 API 실행";
  setText("#modeBadge", state.status === "idle" ? "데모 준비 완료" : state.source === "live" ? liveMode : "내장 예시 실행");
  setText("#connectionStatus", state.status === "idle" ? "가상 환경 준비 완료" : state.source === "live" ? "이벤트 연결됨" : "가상 환경만 사용");
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
    const terminalMode = state.mode === "offline_demo" ? "OpenAI 설명 없이 실행" : "실시간 API";
    setText("#connectionStatus", state.status === "complete" ? `${terminalMode} · 완료` : `${terminalMode} · 실패`);
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
    if (response.status === 422) {
      clearTimeout(timeout);
      showInputError("입력 내용을 확인해 주세요. 저장소 주소, 브랜치 또는 커밋, 맡길 일을 모두 입력해야 합니다.");
      return "rejected";
    }
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
      else setText("#connectionStatus", "연결 종료 · 받은 이벤트는 보존됨");
    };
    return "started";
  } catch {
    clearTimeout(timeout);
    return "unavailable";
  }
}

async function runMission(target) {
  const result = await startLiveRun(target);
  if (result === "unavailable") playFixture(target);
}

function showInputError(message = "") {
  const error = $("#inputError");
  error.hidden = !message;
  error.textContent = message;
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
  const validation = validateTargetInput(target);
  showInputError(validation?.message || "");
  if (validation) {
    const field = validation.field === "repository"
      ? $("#repositoryInput")
      : validation.field === "ref"
        ? $("#refInput")
        : $("#missionInput");
    field?.focus();
    return;
  }
  runMission(target);
});

$("#resetButton").addEventListener("click", () => {
  clearRun();
  state = createInitialState(lastTarget);
  $("#repositoryInput").value = lastTarget.repositoryUrl;
  $("#refInput").value = lastTarget.requestedRef;
  $("#missionInput").value = lastTarget.mission;
  showInputError();
  setText("#runClock", "00:00");
  render();
});

$("#replayButton").addEventListener("click", () => playFixture(lastTarget, { speed: 270 }));

$("#copyStreamButton").addEventListener("click", async () => {
  const raw = state.events.map((event) => JSON.stringify(event.raw)).join("\n");
  try {
    await navigator.clipboard.writeText(raw);
    setText("#copyStreamButton", "복사됨");
    setTimeout(() => setText("#copyStreamButton", "복사"), 1200);
  } catch {
    setText("#copyStreamButton", "복사 실패");
  }
});

render();
