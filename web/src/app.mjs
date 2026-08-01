import {
  createCakeCrashFixture,
  createInitialState,
  createUnsupportedFixture,
  isDocumentedUnsupportedMission,
  formatRunInput,
  getInitialTarget,
  reduceRunState,
  validateTargetInput,
} from "./core.mjs";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const OUTCOME_STATUS_LABELS = Object.freeze({
  accepted: "접수 완료",
  ambiguous: "추가 근거 필요",
  budget_exhausted: "실험 종료",
  compatible_candidate: "호환 후보",
  complete: "완료",
  failed: "진행하지 못함",
  fallback: "예시로 계속",
  fallback_complete: "예시 확인 완료",
  fixture_only: "예시만 확인",
  measured: "문제 발견",
  no_failure_observed: "발견된 문제 없음",
  not_analyzed: "분석하지 않음",
  not_run: "진행하지 않음",
  pending: "확인 전",
  pinned: "버전 확인 완료",
  profiling: "동작 확인 중",
  ready: "시작 전",
  running: "실행 중",
  unsupported: "아직 지원하지 않음",
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
  "local-demo": "로컬 데모 소스",
  "local-example-fixture": "예시 Agent repo fixture",
  "github-api": "GitHub에서 확인",
  "github-http-404": "저장소를 찾지 못함",
  "github-unavailable": "GitHub 연결 실패",
});
const configuredApiBase = new URLSearchParams(window.location.search).get("api");
const apiBase = configuredApiBase ? new URL(configuredApiBase, window.location.href) : new URL(window.location.origin);

function apiUrl(path) {
  return new URL(path, apiBase).href;
}

const initialTarget = getInitialTarget(window.location.search);
let state = createInitialState(initialTarget);
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
  renderVerdict("#beforeVerdict", state.beforeVerdict, { neutral: "확인 전", fail: "문제 재현", pass: "문제 없음" });
  renderVerdict("#afterVerdict", state.afterVerdict, { neutral: "확인 전", fail: "다시 실패", pass: "안전장치 통과" });

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
    timeline.innerHTML = '<li class="placeholder">실험을 시작하면 문제가 처음 생긴 도구 호출부터 보여드려요.</li>';
    setText("#evidenceCount", "근거 0개");
    return;
  }
  timeline.innerHTML = state.autopsy
    .map((step) => `<li class="${step.kind === "divergence" ? "divergence" : ""}">${escapeHtml(step.text)}</li>`)
    .join("");
  setText("#evidenceCount", `근거 ${state.autopsy.length}개`);
}

function renderPatch() {
  setText("#patchStatus", state.patch ? "적용 완료 · 다시 확인 중" : "적용 전");
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
    ? !hasValue ? "확인할 항목이 없어요" : allPass ? "모두 통과" : "확인 완료"
    : hasValue ? "확인 중" : "확인 전";
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
    : state.analysisScope === "allowlisted_adapter"
      ? "정확히 고정된 외부 Agent source의 계약을 정적으로 확인한 뒤, 허용된 adapter의 네트워크 차단 local replacement만 실행했습니다. 제출한 Python과 실제 merchant·결제 side effect는 실행하지 않았습니다."
    : state.analysisScope === "compatibility_only"
      ? "공개 저장소의 고정된 metadata로 호환성만 판정했습니다. 저장소 코드나 합성 공격은 실행하지 않았으며, 취약성 또는 안전성을 판정한 결과가 아닙니다."
      : state.analysisScope === "source_unresolved" || state.analysisScope === "source_preflight_failed"
      ? "외부 저장소 또는 allowlist 설정을 확인하지 못해 제출한 에이전트 분석과 실험을 실행하지 않았습니다."
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
  const targetProfile = state.targetProfileView;
  const selection = state.packSelectionView;
  const compatibilitySelection = state.compatibilitySelectionView;
  const compatibility = state.compatibilityReportView;
  const snapshot = state.sourceSnapshotView;
  const adapter = state.adapterContractView;
  const source = state.sourceDescriptorView;
  panel.hidden = !report && !behavior && !experiment && !targetProfile && !selection && !compatibilitySelection && !compatibility && !adapter;
  if (panel.hidden) return;

  const profile = behavior || targetProfile || report?.profile || {
    agentName: "에이전트를 아직 확인하지 못했어요",
    capabilities: [],
    tools: [],
    sideEffectTools: [],
    declaredCapabilities: [],
    permissions: {},
  };
  const capabilityText = profile.capabilities?.length
    ? profile.capabilities
        .map((capability) => `${capability.tool} [${capability.categories.map((category) => CATEGORY_LABELS[category] || category).join(", ") || "분류되지 않음"}]`)
        .join(" · ")
    : profile.tools?.join(" · ")
      || profile.sideEffectTools?.join(" · ")
      || profile.declaredCapabilities?.join(" · ")
      || "확인된 기능이 없어요";
  const permissions = Object.keys(profile.permissions || {}).length
    ? Object.entries(profile.permissions)
        .map(([key, value]) => key === "max_spend_krw" && typeof value === "number"
          ? `최대 결제 금액 ${won.format(value)}`
          : `${key}: ${value}`)
        .join(" · ")
    : "설정된 권한이 없어요";
  const termination = report?.experiment.termination;
  const observedItems = (report?.observed.items || [])
    .map((item) => `${INVARIANT_LABELS[item.invariant] || item.invariant}: 실제 ${compactJson(item.actual)} / 기준 ${item.expected} (${item.evidence})`)
    .join("\n");
  const sourceStatus = source
    ? RESOLVER_LABELS[source.resolver] || source.resolver
    : behavior?.baselineObserved
      ? "기본 동작 확인 완료"
      : "기본 동작을 확인하지 못했어요";
  const adapterLabel = adapter
    ? ` · ADAPTER ${adapter.adapterId} · network ${adapter.networkAccess}`
    : "";

  setText("#reportAgent", profile.agentName);
  setText(
    "#reportSourceRef",
    targetProfile
      ? `${source?.sourceRef || targetProfile.sourceRef} · ${targetProfile.profileLabel} · ${sourceStatus}${adapterLabel}`
      : behavior
      ? `${source?.sourceRef || behavior.sourceRef} · ${sourceStatus}${adapterLabel}`
      : `${source?.sourceRef || "LabReport agent card"}${adapterLabel}`,
  );
  setText("#reportCapabilities", capabilityText);
  setText(
    "#reportSourceSnapshot",
    snapshot
      ? `${snapshot.mode.toUpperCase()} · ${snapshot.files.length} file(s) · ${snapshot.totalBytes} bytes · ${snapshot.executionScope}${adapter ? ` · ${adapter.entrypoint}` : ""}`
      : adapter
        ? `${adapter.entrypoint} · ${adapter.executionMode}`
        : "source snapshot event 대기",
  );
  setText(
    "#reportBudget",
    report
      ? `실행 ${report.experiment.runs}회 · 비용 ${report.experiment.costUnits}`
      : compatibility
      ? `실험 ${compatibility.experimentsRun}회 · 호환성만 확인`
      : selection?.budget
      ? `최대 실험 ${selection.budget.maxExperiments ?? "?"}회 · 최대 도구 호출 ${selection.budget.maxToolCalls ?? "?"}회`
      : `${MISSION_FAMILY_LABELS[behavior?.missionFamily] || behavior?.missionFamily || targetProfile?.status || "정보"} · 확인 완료`,
  );
  setText("#reportPermissions", permissions);
  setText(
    "#reportTermination",
    termination?.reason === "coverage_complete"
      ? "확인 완료"
      : compatibility
        ? OUTCOME_STATUS_LABELS[compatibility.status] || compatibility.status
        : selection?.stopReason
          ? OUTCOME_STATUS_LABELS[selection.stopReason] || selection.stopReason
          : experiment
            ? "실험 계획을 세웠어요"
            : behavior
              ? "에이전트를 확인했어요"
              : "실험을 아직 마치지 않았어요",
  );

  const experimentCard = $("#selectedExperimentCard");
  experimentCard.hidden = !experiment && !selection && !compatibilitySelection;
  if (experiment) {
    const fault = experiment.faults[0];
    setText(
      "#reportExperiment",
      fault ? `${fault.kind === "commit_then_timeout" ? "결제 직후 응답이 끊기는 상황" : fault.kind} → ${fault.targetTool}` : "재현할 수 있는 실패 유형이 없어요",
    );
    setText(
      "#reportExperimentMeta",
      `${experiment.scenarioId} · 시드 ${experiment.seed ?? "?"} · 최대 ${experiment.maxTurns ?? "?"}회 · ${experiment.singleVariable ? "오류 1개만 변경" : "오류 여러 개 변경"}`,
    );
    setText("#reportExperimentReason", `선택한 이유 · ${experiment.toolChoiceReason}`);
    setText("#reportExperimentEvidence", `확인할 내용 · ${experiment.expectedEvidence}`);
  } else if (selection) {
    setText(
      "#reportExperiment",
      selection.packId
        ? `${(selection.domainKind || "unknown").toUpperCase()} PACK ${selection.packId}`
        : selection.ambiguous ? "PACK SELECTION AMBIGUOUS" : "NO PACK SELECTED",
    );
    setText(
      "#reportExperimentMeta",
      `${selection.executable ? "EXECUTABLE" : "TERMINAL"} · ${selection.fallback}`,
    );
    setText("#reportExperimentReason", `선택한 이유 · ${selection.why}`);
    setText("#reportExperimentEvidence", `확인할 내용 · ${selection.expect}`);
  } else if (compatibilitySelection) {
    setText(
      "#reportExperiment",
      compatibilitySelection.selectedDomain
        ? `${compatibilitySelection.selectedDomain.toUpperCase()} PACK ${compatibilitySelection.packId || "CANDIDATE"}`
        : "NO PACK SELECTED",
    );
    setText(
      "#reportExperimentMeta",
      `${compatibilitySelection.status.toUpperCase()} · ${compatibilitySelection.maxExperiments} experiments · ${compatibilitySelection.fallback}`,
    );
    setText("#reportExperimentReason", `선택한 이유 · ${compatibilitySelection.why}`);
    setText("#reportExperimentEvidence", `확인할 내용 · ${compatibilitySelection.expect}`);
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
        : assessment.unknownReason || "판단할 근거가 없어요";
      header.append(name, verdict);
      card.append(header, detail);
      fragment.appendChild(card);
    });
    assessmentGrid.replaceChildren(fragment);
  }

  const claimGrid = $("#claimGrid");
  const residualRisk = $("#reportResidualRisk");
  claimGrid.hidden = !report && !compatibility;
  residualRisk.hidden = !report && !compatibility;
  if (compatibility && !report) {
    setText(
      "#reportObserved",
      `${compatibility.message}\n발견 ${compatibility.findings.length}건 · 실험 ${compatibility.experimentsRun}회`,
    );
    setText(
      "#reportHypothesis",
      compatibility.compatibilityClaims.join("\n")
        || "도메인을 판단할 근거가 부족해 취약점 가설을 만들지 않았어요.",
    );
    setText("#reportProposed", "호환성만 확인하는 단계에서는 패치를 제안하지 않아요.");
    setText("#reportVerified", "저장소 코드와 합성 공격을 실행하지 않았어요.");
    setText("#reportResidualRisk", `판정 범위 · ${compatibility.claimBoundary}`);
    return;
  }
  if (!report) return;
  setText(
    "#reportObserved",
    observedItems ? `${report.observed.headline}\n${observedItems}` : report.observed.headline,
  );
  setText(
    "#reportHypothesis",
    report.hypothesis
      ? `${DIAGNOSIS_LABELS[report.hypothesis.category] || report.hypothesis.category} · ${report.hypothesis.statement}`
      : "근거가 부족해 원인을 단정하지 않았어요.",
  );
  setText(
    "#reportProposed",
    report.proposedPatch ? compactJson(report.proposedPatch) : "적용할 안전장치를 아직 찾지 못했어요.",
  );
  setText(
    "#reportVerified",
    report.verification
      ? `${report.verification.accepted ? "모두 통과" : "다시 확인 필요"} · ${report.verification.totalGates}개 중 ${report.verification.passedGates}개 통과`
      : "안전장치를 적용한 뒤 다시 확인한 결과가 없어요.",
  );
  const limits = [...report.residualRisk, ...report.unsupportedScope];
  setText(
    "#reportResidualRisk",
    limits.length ? `아직 확인하지 못한 위험 · ${limits.join(" · ")}` : "지금까지 확인된 추가 위험은 없어요",
  );
}

function renderStream() {
  const stream = $("#rawStream");
  if (!state.events.length) {
    stream.innerHTML = '<div class="stream-empty">실험을 시작하면 실시간 기록이 여기에 쌓여요.</div>';
    setText("#eventCount", "기록 0개");
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
  setText("#eventCount", `기록 ${state.events.length}개`);
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
  setText("#targetRepo", state.target.repositoryUrl || "입력 전");
  setText("#targetRef", state.target.requestedRef || "입력 전");
  setText(
    "#targetSha",
    state.target.resolvedSha || (state.source === "fixture" && state.status !== "idle"
      ? "내장 예시 · 저장소는 확인하지 않았어요"
      : "확인 전"),
  );
  const notice = $("#runNotice");
  notice.hidden = !state.terminalNotice;
  notice.dataset.kind = state.terminalNotice?.kind || "";
  notice.textContent = state.terminalNotice?.message || "";
  setText("#runId", state.runId ? `실행 ID ${state.runId}` : "실행 ID —");
  const liveMode = state.mode === "offline_demo"
    ? "준비된 설명으로 진행 중"
    : state.mode === "compatibility_only"
      ? "호환성만 판정 중"
      : "실시간 분석 중";
  setText(
    "#modeBadge",
    state.status === "idle"
      ? "시작할 준비가 됐어요"
      : state.source === "live"
        ? liveMode
        : state.status === "complete"
          ? "내장 예시 확인 완료"
          : "내장 예시 확인 중",
  );
  setText("#connectionStatus", state.status === "idle" ? "실험을 시작해 주세요" : state.source === "live" ? "실시간 기록을 받고 있어요" : "가상 환경에서 진행 중");
  $("#runButton").disabled = state.status === "running";
  $("#replayButton").disabled = state.status !== "complete";
}

function dispatch(event) {
  state = reduceRunState(state, event);
  render();
  const terminalEvent = event.type === "run_completed"
    || event.type === "run.completed"
    || ((event.type === "run_failed" || event.type === "run.failed")
      && (event.data?.status ?? event.payload?.status) !== "offline_demo");
  if (terminalEvent) {
    if (state.source === "live") {
      eventSource?.close();
      eventSource = null;
    }
    clearInterval(clockTimer);
    const terminalMode = state.source === "live"
      ? state.mode === "offline_demo" ? "준비된 설명" : "실시간 분석"
      : "내장 예시";
    setText("#connectionStatus", state.status === "complete" ? `${terminalMode} · 완료` : `${terminalMode} · 진행하지 못함`);
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
  const events = isDocumentedUnsupportedMission(target.mission)
    ? createUnsupportedFixture(target.mission, target)
    : createCakeCrashFixture(target.mission, target);
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
      showInputError("입력하지 않은 항목이 있어요. 저장소 주소, 브랜치나 커밋, 에이전트에게 시킬 일을 모두 입력해 주세요.");
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
      else setText("#connectionStatus", "연결이 끊겼어요 · 받은 기록은 그대로 남겼어요");
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
    setText("#copyStreamButton", "복사했어요");
    setTimeout(() => setText("#copyStreamButton", "복사"), 1200);
  } catch {
    setText("#copyStreamButton", "다시 시도");
  }
});

$("#repositoryInput").value = initialTarget.repositoryUrl;
$("#refInput").value = initialTarget.requestedRef;
$("#missionInput").value = initialTarget.mission;
render();
