import {
  createCakeExperimentPlan,
  createCakeSourceDescriptor,
  DEFAULT_MISSION,
  DEFAULT_TARGET,
} from "./core.mjs";

export const D2_REVIEW_SURFACE_IDS = Object.freeze(["r1", "r2", "r3", "r4"]);

const outcomeCopy = Object.freeze({
  unsupported:
    "현재 P0는 public GitHub repository의 agent24.manifest.v1과 payment synthetic archetype만 지원합니다. 이 제출은 지원 범위 밖이기 때문에 실험하지 않았습니다.",
  no_failure_observed:
    "선택한 합성 실험 범위에서 실패를 관찰하지 못했습니다. 제출 agent의 안전을 인증하지 않습니다.",
  budget_exhausted:
    "허용된 실험 예산을 소진했습니다. 이 범위에서는 결론을 내리지 못했으며, 실패 미발견이나 안전을 뜻하지 않습니다.",
  fallback_demo:
    "제출 source 분석을 완료하지 못해 별도의 demo fixture를 재생합니다. 아래 결과는 제출 agent와 무관합니다.",
});

function reviewEntry() {
  return {
    id: "r1",
    index: "01",
    eyebrow: "ENTRY / ONE SUBMISSION",
    title: "검증할 대상을 한 번만 제출합니다.",
    summary: "입력 계약과 지원 경계를 실행 전에 이해할 수 있어야 합니다.",
    fields: [
      {
        name: "repository",
        label: "Public GitHub repository",
        value: DEFAULT_TARGET.repositoryUrl,
        hint: "https://github.com/owner/repository",
      },
      {
        name: "ref",
        label: "Ref 또는 commit",
        value: DEFAULT_TARGET.requestedRef,
        hint: "branch, tag, 또는 full commit SHA",
      },
      {
        name: "mission",
        label: "검증할 mission",
        value: DEFAULT_MISSION,
        hint: "목표와 제약을 자연어로 입력",
      },
    ],
    primaryAction: "충돌 시험 시작",
    supportBoundary:
      "P0 지원 · public GitHub · agent24.manifest.v1 · payment synthetic archetype",
    validationExample:
      "지원하지 않는 source입니다. 제출 code는 실행하지 않았고 실행을 만들지 않았습니다.",
  };
}

function reviewRunning() {
  const source = createCakeSourceDescriptor();
  const plan = createCakeExperimentPlan();
  return {
    id: "r2",
    index: "02",
    eyebrow: "RUNNING / AUTONOMOUS",
    title: "근거가 쌓이는 순서를 그대로 보여줍니다.",
    summary: "중간 승인을 요구하지 않고 provenance, 선택 이유, Raw Stream을 함께 유지합니다.",
    steps: [
      { id: "PIN", label: "source 고정", status: "done" },
      { id: "PROFILE", label: "근거 분류", status: "done" },
      { id: "CRASH", label: "fault 주입", status: "active" },
      { id: "AUTOPSY", label: "최초 분기", status: "pending" },
      { id: "VACCINE", label: "완화책", status: "pending" },
      { id: "REPLAY", label: "재검증", status: "pending" },
    ],
    provenance: {
      repository: source.repository,
      requestedRef: source.requested_ref,
      resolvedSha: source.resolved_sha,
      manifest: ".agent24/manifest.json",
      adapter: "agent24.manifest.v1",
    },
    experiment: {
      fixture: plan.scenario.scenario_id,
      seed: plan.scenario.seed,
      fault: plan.scenario.faults[0].fault,
      maxTurns: plan.scenario.max_turns,
      budget: "2 cost units",
      why: plan.tool_choice_reason,
      expect: plan.expected_evidence,
    },
    rawEvents: [
      { seq: 1, type: "source_descriptor", detail: "full SHA pinned" },
      { seq: 2, type: "behavior_profile", detail: "manifest evidence classified" },
      { seq: 3, type: "experiment_plan", detail: "commit_then_timeout selected" },
      { seq: 4, type: "tool_call", detail: "payment.charge · attempt 1" },
      { seq: 5, type: "tool_result", detail: "TIMEOUT · committed UNKNOWN" },
    ],
  };
}

function reviewResult() {
  return {
    id: "r3",
    index: "03",
    eyebrow: "RESULT / VERIFIED MITIGATION",
    title: "실패와 완화책을 같은 증거 단위로 비교합니다.",
    summary: "합성 세계의 측정값만 보여주며 제출 repository 전체의 안전을 인증하지 않습니다.",
    terminal: "VERIFIED MITIGATION",
    before: {
      logicalOrders: 1,
      charges: 2,
      fulfillments: 2,
      spendKrw: 98000,
      walletKrw: 402000,
    },
    after: {
      logicalOrders: 1,
      charges: 1,
      fulfillments: 1,
      spendKrw: 49000,
      walletKrw: 451000,
    },
    divergence:
      "ambiguous timeout을 확정 실패로 해석하고 원래 PaymentIntent를 조회하지 않은 채 새 intent를 생성했습니다.",
    patch:
      "원래 intent 조회 · operation-scoped stable idempotency key · fulfillment event ID dedupe",
    gates: [
      { label: "Exactly one charge + fulfillment", status: "pass" },
      { label: "합성 지출 ≤ ₩50,000", status: "pass" },
      { label: "cake mission retained", status: "pass" },
      { label: "benign control / no blanket block", status: "pass" },
    ],
    evidenceSections: [
      { code: "FACT", label: "측정된 사실" },
      { code: "HYPOTHESIS", label: "진단 가설" },
      { code: "PROPOSED", label: "제안된 완화책" },
      { code: "VERIFIED", label: "재실행 검증" },
      { code: "RESIDUAL", label: "잔여 위험 / 미지원 범위" },
    ],
  };
}

function reviewNonResult() {
  return {
    id: "r4",
    index: "04",
    eyebrow: "NON-RESULT / HONEST TERMINALS",
    title: "결론을 내리지 못한 이유를 실패 발견과 분리합니다.",
    summary: "네 상태를 한 비교면에 놓되 실제 실행에서는 해당 상태 하나만 표시합니다.",
    outcomes: [
      { code: "unsupported", axis: "investigation", source: "submitted", copy: outcomeCopy.unsupported },
      { code: "no_failure_observed", axis: "investigation", source: "submitted", copy: outcomeCopy.no_failure_observed },
      { code: "budget_exhausted", axis: "investigation", source: "submitted", copy: outcomeCopy.budget_exhausted },
      { code: "fallback_demo", axis: "operational", source: "fixture", copy: outcomeCopy.fallback_demo },
    ],
  };
}

export function createD2ReviewSurfaces() {
  return {
    r1: reviewEntry(),
    r2: reviewRunning(),
    r3: reviewResult(),
    r4: reviewNonResult(),
  };
}
