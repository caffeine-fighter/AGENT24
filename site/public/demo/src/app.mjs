import {
  createCakeCrashFixture,
  createInitialState,
  createUnsupportedFixture,
  isDocumentedUnsupportedMission,
  reduceRunState,
  validateTargetInput,
} from "./core.mjs";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const OUTCOME_STATUS_LABELS = Object.freeze({
  accepted: "저장소 접수",
  ambiguous: "근거 부족",
  budget_exhausted: "실험 한도 도달",
  compatible_candidate: "지원 가능성 있음",
  complete: "완료",
  failed: "실험 중단",
  fallback: "내장 예시 사용",
  fallback_complete: "내장 예시 완료",
  fixture_only: "내장 예시",
  measured: "위험 발견",
  no_failure_observed: "위험 미발견",
  not_analyzed: "분석 안 함",
  not_run: "실험 안 함",
  pending: "확인 전",
  pinned: "커밋 확인",
  profiling: "에이전트 파악 중",
  ready: "실험 전",
  running: "실험 중",
  unsupported: "현재 지원 안 함",
});
const ASSESSMENT_LABELS = Object.freeze({
  retry_behavior: "재시도 처리",
  idempotency_usage: "중복 요청 방지",
  reconciliation_usage: "처리 결과 재확인",
  untrusted_input_handling: "외부 입력 검증",
  loop_budget: "반복 횟수 제한",
});
const ASSESSMENT_VALUE_LABELS = Object.freeze({
  absent: "찾지 못함",
  present: "확인됨",
  unknown: "근거 부족",
});
const CATEGORY_LABELS = Object.freeze({
  privileged_sink: "권한이 필요한 기능",
  side_effect: "외부에 영향을 주는 기능",
  untrusted_source: "외부에서 들어온 정보",
});
const INVARIANT_LABELS = Object.freeze({
  "platform.exactly_once_payment": "결제 중복 방지",
  "task.max_spend_krw": "결제 한도",
  "task.purchase_count": "주문 횟수",
  "task.total_spend": "총 결제 금액",
});
const MISSION_FAMILY_LABELS = Object.freeze({ purchase: "구매" });
const DIAGNOSIS_LABELS = Object.freeze({ duplicate_side_effect: "같은 작업이 두 번 실행됨" });
const RESOLVER_LABELS = Object.freeze({
  fixture: "내장 예시",
  "github-api": "GitHub 공개 정보로 확인",
  "github-http-404": "저장소 확인 실패",
  "github-unavailable": "GitHub에 연결할 수 없음",
});
const PERMISSION_LABELS = Object.freeze({
  max_purchase_count: "최대 주문 횟수",
  max_spend_krw: "최대 결제 금액",
});
const DOMAIN_LABELS = Object.freeze({
  adhoc: "기타 작업",
  life: "일상 작업",
  purchase: "구매",
  research: "자료 조사",
  stock: "주식 분석",
  ticket: "예매",
  unknown: "분류 전",
});
const EXECUTION_SCOPE_LABELS = Object.freeze({
  manifest_only: "설정 파일만 확인",
  none: "실행하지 않음",
  static_metadata_only: "공개 정보만 확인",
  synthetic_archetype: "가상 행동 모델로 재현",
});
const SOURCE_MODE_LABELS = Object.freeze({
  manifest: "설정 파일",
  manifest_only: "설정 파일",
  metadata: "공개 정보",
  metadata_only: "공개 정보",
  none: "저장소 정보 없음",
  synthetic: "가상 모델",
});
const PROFILE_LABELS = Object.freeze({
  "LAB-INFERRED STATIC PROFILE": "공개 정보로 추정한 프로필",
  "OWNER MANIFEST": "저장소가 제공한 설정",
  "PROFILE ORIGIN UNKNOWN": "프로필 출처를 확인하지 못했어요",
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
  if (element && element.textContent !== String(value)) element.textContent = value;
}

function readableIdentifier(value, fallback = "정보 없음") {
  if (!value) return fallback;
  return String(value).replaceAll("_", " ").replaceAll("-", " ");
}

function formatAgentName(value) {
  if (!value) return "에이전트 정보 없음";
  if (["synthetic-fixture-fallback", "submitted-agent"].includes(value)) {
    return value === "synthetic-fixture-fallback" ? "내장 예시 에이전트" : "제출한 에이전트";
  }
  return value;
}

function formatPermission([key, value]) {
  const label = PERMISSION_LABELS[key] || readableIdentifier(key);
  if (key === "max_spend_krw" && typeof value === "number") return `${label} ${won.format(value)}`;
  if (key === "max_purchase_count" && typeof value === "number") return `${label} ${value}회`;
  return `${label} ${value}`;
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes.toLocaleString("ko-KR")}바이트`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)}MB`;
}

function formatDomain(value) {
  return DOMAIN_LABELS[value] || readableIdentifier(value, "분류 전");
}

function formatFallback(value) {
  if (!value || value === "none") return "대체 계획 없음";
  if (value === "terminal") return "여기서 종료";
  if (value === "terminal_compatibility_only") return "지원 가능성만 확인하고 종료";
  return readableIdentifier(value);
}

function formatProfileLabel(value) {
  return PROFILE_LABELS[value] || readableIdentifier(value, "프로필 출처를 확인하지 못했어요");
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
  renderVerdict("#beforeVerdict", state.beforeVerdict, { neutral: "실험 전", fail: "실패 재현", pass: "문제 미발견" });
  renderVerdict("#afterVerdict", state.afterVerdict, { neutral: "실험 전", fail: "다시 실패", pass: "모두 통과" });

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
    timeline.innerHTML = '<li class="placeholder">문제가 시작된 도구 호출부터 시간순으로 보여드려요.</li>';
    setText("#evidenceCount", "근거 0개");
    return;
  }
  timeline.innerHTML = state.autopsy
    .map((step) => `<li class="${step.kind === "divergence" ? "divergence" : ""}">${escapeHtml(step.text)}</li>`)
    .join("");
  setText("#evidenceCount", `근거 ${state.autopsy.length}개`);
}

function renderPatch() {
  setText("#patchStatus", state.patch ? "적용됨 · 결과 확인 중" : "적용 전");
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
    ? !hasValue ? "확인할 조건 없음" : allPass ? "모두 통과" : "확인 완료"
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
    ? "지금 보는 결과는 제출한 저장소가 아니라 내장 예시예요. 기록은 가상 환경에서 만든 것이며, 이 결과만으로 안전을 보장할 수 없어요."
    : state.analysisScope === "compatibility_only"
      ? "공개 저장소에서 확인할 수 있는 정보만 살펴봤어요. 코드를 실행하거나 가상 공격을 재현하지 않았으므로, 취약점이나 안전성을 판단한 결과가 아니에요."
      : "가상 환경에서만 진행해요. 저장소와 허용된 설정만 읽고 에이전트의 동작을 재현해요. 문제를 찾지 못해도 안전이 보장되는 것은 아니에요.";
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
  const source = state.sourceDescriptorView;
  panel.hidden = !report && !behavior && !experiment && !targetProfile && !selection && !compatibilitySelection && !compatibility;
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
    ? Object.entries(profile.permissions).map(formatPermission).join(" · ")
    : "별도로 허용된 권한 없음";
  const termination = report?.experiment.termination;
  const observedItems = (report?.observed.items || [])
    .map((item) => `${INVARIANT_LABELS[item.invariant] || readableIdentifier(item.invariant)}: 측정값 ${compactJson(item.actual)} · 기준 ${item.expected}\n근거 ${item.evidence}`)
    .join("\n");
  const sourceStatus = source
    ? RESOLVER_LABELS[source.resolver] || source.resolver
    : behavior?.baselineObserved
      ? "기본 동작 확인 완료"
      : "기본 동작을 확인하지 못했어요";

  setText("#reportAgent", formatAgentName(profile.agentName));
  setText(
    "#reportSourceRef",
    targetProfile
      ? `${source?.sourceRef || targetProfile.sourceRef} · ${formatProfileLabel(targetProfile.profileLabel)}`
      : behavior
      ? `${source?.sourceRef || behavior.sourceRef} · ${sourceStatus}`
      : source?.sourceRef || "보고서에 기록된 저장소",
  );
  setText("#reportCapabilities", capabilityText);
  setText(
    "#reportSourceSnapshot",
    snapshot
      ? `${SOURCE_MODE_LABELS[snapshot.mode] || readableIdentifier(snapshot.mode)} · 파일 ${snapshot.files.length}개 · ${formatBytes(snapshot.totalBytes)} · ${EXECUTION_SCOPE_LABELS[snapshot.executionScope] || readableIdentifier(snapshot.executionScope)}`
      : "저장소 확인 범위 없음",
  );
  setText(
    "#reportBudget",
    report
      ? `실험 ${report.experiment.runs}회 · 사용량 ${report.experiment.costUnits}단위`
      : compatibility
      ? `가상 실험 ${compatibility.experimentsRun}회 · 지원 가능성만 확인`
      : selection?.budget
      ? `실험 최대 ${selection.budget.maxExperiments ?? "?"}회 · 도구 호출 최대 ${selection.budget.maxToolCalls ?? "?"}회`
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
              : "실험 진행 중",
  );

  const experimentCard = $("#selectedExperimentCard");
  experimentCard.hidden = !experiment && !selection && !compatibilitySelection;
  if (experiment) {
    const fault = experiment.faults[0];
    setText(
      "#reportExperiment",
      fault ? `${fault.kind === "commit_then_timeout" ? "결제 직후 응답 끊김" : readableIdentifier(fault.kind)} → ${fault.targetTool}` : "재현할 실패를 찾지 못했어요",
    );
    setText(
      "#reportExperimentMeta",
      `${experiment.scenarioId} · 재현값 ${experiment.seed ?? "?"} · 최대 ${experiment.maxTurns ?? "?"}단계 · ${experiment.singleVariable ? "오류 1개만 변경" : "오류 여러 개 변경"}`,
    );
    setText("#reportExperimentReason", `선택한 이유 · ${experiment.toolChoiceReason}`);
    setText("#reportExperimentEvidence", `확인할 내용 · ${experiment.expectedEvidence}`);
  } else if (selection) {
    setText(
      "#reportExperiment",
      selection.packId
        ? `${formatDomain(selection.domainKind)} 실험`
        : selection.ambiguous ? "실험을 하나로 고르지 못했어요" : "맞는 실험을 찾지 못했어요",
    );
    setText(
      "#reportExperimentMeta",
      `${selection.packId ? `실험 모음 ${selection.packId} · ` : ""}${selection.executable ? "바로 실행 가능" : "실행하지 않고 종료"} · ${formatFallback(selection.fallback)}`,
    );
    setText("#reportExperimentReason", `선택한 이유 · ${selection.why}`);
    setText("#reportExperimentEvidence", `확인할 내용 · ${selection.expect}`);
  } else if (compatibilitySelection) {
    setText(
      "#reportExperiment",
      compatibilitySelection.selectedDomain
        ? `${formatDomain(compatibilitySelection.selectedDomain)} 실험 가능성 확인`
        : "맞는 실험을 찾지 못했어요",
    );
    setText(
      "#reportExperimentMeta",
      `${OUTCOME_STATUS_LABELS[compatibilitySelection.status] || readableIdentifier(compatibilitySelection.status)} · 실험 최대 ${compatibilitySelection.maxExperiments}회 · ${formatFallback(compatibilitySelection.fallback)}`,
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
      name.textContent = ASSESSMENT_LABELS[assessment.name] || readableIdentifier(assessment.name);
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
        || "어떤 실험이 맞는지 판단할 근거가 부족해 원인 가설을 만들지 않았어요.",
    );
    setText("#reportProposed", "지원 가능성만 살펴본 단계라 안전장치를 제안하지 않았어요.");
    setText("#reportVerified", "저장소 코드를 실행하거나 가상 공격을 재현하지 않았어요.");
    setText("#reportResidualRisk", `확인 범위 · ${compatibility.claimBoundary}`);
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
    limits.length ? `아직 확인하지 못한 위험 · ${limits.join(" · ")}` : "현재 실험 범위에서 추가 위험은 찾지 못했어요",
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
    const rawPayload = node.querySelector("pre");
    rawPayload.textContent = JSON.stringify(event.raw, null, 2);
    if (["source.descriptor", "behavior.profile", "experiment.plan", "lab.report"].includes(event.type)) {
      rawPayload.tabIndex = 0;
      rawPayload.setAttribute("aria-label", `${event.wire_type || event.type} 원본 JSON`);
    }
    fragment.appendChild(node);
  });
  stream.replaceChildren(fragment);
  stream.scrollTop = stream.scrollHeight;
  setText("#eventCount", `기록 ${state.events.length}개`);
}

function render() {
  document.body.dataset.running = String(state.status === "running");
  $("#resultSurface").hidden = state.status === "idle" && state.events.length === 0;
  const hasExperimentEvidence = Boolean(
    state.experimentPlanView
    || state.baselineEvidence
    || state.oracleReport
    || state.protectedReplay
    || (state.findingReport && Number(state.findingReport.experiments_run) > 0),
  );
  $("#worldGrid").hidden = !hasExperimentEvidence;
  $("#evidenceGrid").hidden = !state.autopsy.length && !Object.values(state.checks).some((value) => value !== null);
  renderPhases();
  renderAssets();
  renderAutopsy();
  renderPatch();
  renderChecks();
  renderOutcomes();
  renderLabReport();
  renderStream();
  setText("#targetRepo", state.target.repositoryUrl || "—");
  setText("#targetRef", state.target.requestedRef || "—");
  setText(
    "#targetSha",
    state.target.resolvedSha || (state.source === "fixture" && state.status !== "idle"
      ? "내장 예시 · 저장소는 확인하지 않았어요"
      : "—"),
  );
  const notice = $("#runNotice");
  const noticeMessage = state.terminalNotice?.message
    || (state.status === "running" ? state.outcomes.operation.message : "")
    || (["complete", "failed"].includes(state.status) ? state.outcomes.operation.message : "");
  notice.hidden = !noticeMessage;
  notice.dataset.kind = state.terminalNotice?.kind || state.status;
  setText("#runNotice", noticeMessage);
  setText("#runId", state.runId ? `실행 ID ${state.runId}` : "실행 ID —");
  const liveMode = state.mode === "offline_demo" ? "내장 설명 사용 중" : "실시간으로 분석 중";
  setText(
    "#modeBadge",
    state.status === "idle"
      ? "실험 준비 완료"
      : state.source === "live"
        ? liveMode
        : state.status === "complete"
          ? "내장 예시 완료"
          : "내장 예시 실행 중",
  );
  setText("#connectionStatus", state.status === "idle" ? "실험 전" : state.source === "live" ? "실행 기록을 받고 있어요" : "가상 환경에서 실행 중");
  const runButton = $("#runButton");
  runButton.disabled = state.status === "running";
  runButton.setAttribute("aria-busy", String(state.status === "running"));
  runButton.querySelector("span").textContent = state.status === "running" ? "실험 중" : "실험 시작하기";
  $("#resetButton").hidden = !["complete", "failed"].includes(state.status);
  $("#replayButton").hidden = state.status !== "complete";
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
      ? state.mode === "offline_demo" ? "내장 설명" : "실시간 분석"
      : "내장 예시";
    setText("#connectionStatus", state.status === "complete" ? `${terminalMode} 완료` : `${terminalMode} 중단`);
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
  // A hosted Responses API planning turn can take longer than a local fixture.
  // Keep the fallback bounded while allowing the real server-side call to finish.
  const timeout = setTimeout(() => controller.abort(), 22000);
  clearRun();
  state = createInitialState(target);
  state.status = "running";
  state.source = "live";
  state.outcomes.operation = { status: "running", message: "저장소 연결을 확인하고 있어요" };
  lastTarget = { ...target };
  receivedLiveEvent = false;
  render();
  startClock();
  try {
    const response = await fetch(apiUrl("/api/runs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
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
      clearRun();
      state = createInitialState(target);
      render();
      showInputError("빈칸을 모두 채워 주세요. GitHub 저장소, 확인할 버전, 에이전트에게 맡길 일이 필요해요.");
      return "rejected";
    }
    if (!response.ok) throw new Error(`Run API returned ${response.status}`);
    const payload = await response.json();
    const runId = payload.run_id;
    if (!runId) throw new Error("Run API response has no run_id");
    clearTimeout(timeout);
    state.mode = payload.mode || "live";
    state.runId = runId;
    render();
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
      else setText("#connectionStatus", "연결이 끊겼어요. 지금까지 받은 기록은 그대로 남겨뒀어요.");
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

function showInputError(message = "", field = null) {
  const error = $("#inputError");
  for (const input of $$("#missionForm input, #missionForm textarea")) {
    input.removeAttribute("aria-invalid");
    const describedBy = (input.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter((id) => id && id !== "inputError");
    if (describedBy.length) input.setAttribute("aria-describedby", describedBy.join(" "));
    else input.removeAttribute("aria-describedby");
  }
  error.hidden = !message;
  setText("#inputError", message);
  if (message && field) {
    field.setAttribute("aria-invalid", "true");
    const describedBy = new Set((field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
    describedBy.add("inputError");
    field.setAttribute("aria-describedby", [...describedBy].join(" "));
  }
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
  showInputError();
  if (validation) {
    const field = validation.field === "repository"
      ? $("#repositoryInput")
      : validation.field === "ref"
        ? $("#refInput")
        : $("#missionInput");
    showInputError(validation.message, field);
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

$("#replayButton").addEventListener("click", () => runMission(lastTarget));

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

render();
