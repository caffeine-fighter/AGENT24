const won = new Intl.NumberFormat("ko-KR", {
  style: "currency",
  currency: "KRW",
  maximumFractionDigits: 0,
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function surfaceIntro(surface) {
  return `
    <header class="surface-intro">
      <div class="surface-index" aria-hidden="true">${escapeHtml(surface.index)}</div>
      <div>
        <p class="surface-eyebrow">${escapeHtml(surface.eyebrow)}</p>
        <h1>${escapeHtml(surface.title)}</h1>
        <p class="surface-summary">${escapeHtml(surface.summary)}</p>
      </div>
    </header>`;
}

function renderEntry(surface) {
  const fields = surface.fields.map((field) => {
    const control = field.name === "mission"
      ? `<textarea id="review-${field.name}" rows="3" readonly>${escapeHtml(field.value)}</textarea>`
      : `<input id="review-${field.name}" value="${escapeHtml(field.value)}" readonly />`;
    return `
      <div class="review-field" data-field="${escapeHtml(field.name)}">
        <label for="review-${escapeHtml(field.name)}">${escapeHtml(field.label)}</label>
        ${control}
        <small>${escapeHtml(field.hint)}</small>
      </div>`;
  }).join("");

  return `
    <section class="review-surface entry-surface" data-review-surface="r1">
      ${surfaceIntro(surface)}
      <div class="entry-layout">
        <section class="entry-promise" aria-labelledby="entryPromiseTitle">
          <p class="specimen-label">ONE INPUT · AUTONOMOUS AFTER SUBMIT</p>
          <h2 id="entryPromiseTitle">현실에 손대기 전,<br /><em>실패를 먼저 측정합니다.</em></h2>
          <p>검증된 metadata로 선택한 합성 행동 원형만 결정적 세계에서 시험합니다.</p>
          <dl class="boundary-list">
            <div><dt>SOURCE</dt><dd>public GitHub metadata</dd></div>
            <div><dt>EXECUTION</dt><dd>submitted code 실행 안 함</dd></div>
            <div><dt>WORLD</dt><dd>local synthetic only</dd></div>
          </dl>
        </section>
        <form class="review-form" aria-label="외부 Agent 충돌 시험 입력">
          <div class="form-heading">
            <span>01 / TARGET</span>
            <strong>통합 예시</strong>
          </div>
          ${fields}
          <p class="support-boundary">${escapeHtml(surface.supportBoundary)}</p>
          <button type="button">${escapeHtml(surface.primaryAction)} <span aria-hidden="true">↗</span></button>
          <div class="validation-specimen" role="alert">
            <strong>REJECTED BEFORE RUN</strong>
            <span>${escapeHtml(surface.validationExample)}</span>
          </div>
        </form>
      </div>
    </section>`;
}

function renderRunning(surface) {
  const steps = surface.steps.map((step, index) => `
    <li data-step-status="${escapeHtml(step.status)}">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <strong>${escapeHtml(step.id)}</strong>
      <small>${escapeHtml(step.label)}</small>
    </li>`).join("");
  const rawEvents = surface.rawEvents.map((event) => `
    <li>
      <span>#${String(event.seq).padStart(2, "0")}</span>
      <strong>${escapeHtml(event.type)}</strong>
      <small>${escapeHtml(event.detail)}</small>
    </li>`).join("");

  return `
    <section class="review-surface running-surface" data-review-surface="r2">
      ${surfaceIntro(surface)}
      <ol class="journey-rail" aria-label="자율 분석 단계">${steps}</ol>
      <div class="running-layout">
        <div class="running-main">
          <section class="provenance-strip" aria-labelledby="provenanceTitle">
            <header><span>PINNED TARGET</span><strong id="provenanceTitle">SOURCE VERIFIED</strong></header>
            <dl>
              <div><dt>REPOSITORY</dt><dd>${escapeHtml(surface.provenance.repository)}</dd></div>
              <div><dt>REQUESTED REF</dt><dd>${escapeHtml(surface.provenance.requestedRef)}</dd></div>
              <div class="wide"><dt>RESOLVED SHA</dt><dd>${escapeHtml(surface.provenance.resolvedSha)}</dd></div>
              <div><dt>MANIFEST</dt><dd>${escapeHtml(surface.provenance.manifest)}</dd></div>
              <div><dt>ADAPTER</dt><dd>${escapeHtml(surface.provenance.adapter)}</dd></div>
            </dl>
          </section>
          <section class="experiment-card" aria-labelledby="experimentTitle">
            <header>
              <div><span>ACTIVE · CRASH</span><h2 id="experimentTitle">commit_then_timeout</h2></div>
              <strong>SEED ${escapeHtml(surface.experiment.seed)}</strong>
            </header>
            <div class="experiment-meta">
              <span>${escapeHtml(surface.experiment.fixture)}</span>
              <span>${escapeHtml(surface.experiment.maxTurns)} turns</span>
              <span>${escapeHtml(surface.experiment.budget)}</span>
            </div>
            <dl class="why-expect">
              <div><dt>WHY</dt><dd>${escapeHtml(surface.experiment.why)}</dd></div>
              <div><dt>EXPECT</dt><dd>${escapeHtml(surface.experiment.expect)}</dd></div>
            </dl>
          </section>
        </div>
        <aside class="raw-panel" aria-labelledby="rawTitle">
          <header><span>UNEDITED</span><h2 id="rawTitle">RAW API STREAM</h2></header>
          <ol role="log" aria-live="polite">${rawEvents}</ol>
          <p>published tool-call / tool-result payload identity preserved</p>
        </aside>
      </div>
    </section>`;
}

function metric(label, value, kind = "") {
  return `<div class="metric ${kind}"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
}

function renderWorld(title, label, world, tone) {
  return `
    <section class="world-dossier ${tone}" aria-label="${escapeHtml(title)}">
      <header><span>${escapeHtml(label)}</span><h2>${escapeHtml(title)}</h2></header>
      <dl class="metric-grid">
        ${metric("LOGICAL ORDER", world.logicalOrders)}
        ${metric("CHARGES", world.charges, world.charges > 1 ? "critical" : "")}
        ${metric("FULFILLMENTS", world.fulfillments, world.fulfillments > 1 ? "critical" : "")}
        ${metric("TOTAL SPEND", won.format(world.spendKrw), world.spendKrw > 50000 ? "critical" : "")}
        ${metric("WALLET", won.format(world.walletKrw))}
      </dl>
    </section>`;
}

function renderResult(surface) {
  const gates = surface.gates.map((gate) => `
    <li data-gate-status="${escapeHtml(gate.status)}">
      <span aria-hidden="true">✓</span><strong>${escapeHtml(gate.label)}</strong><small>PASS</small>
    </li>`).join("");
  const evidence = surface.evidenceSections.map((section) => `
    <li class="evidence-key">
      <span>${escapeHtml(section.code)}</span><strong>${escapeHtml(section.label)}</strong>
    </li>`).join("");

  return `
    <section class="review-surface result-surface" data-review-surface="r3">
      ${surfaceIntro(surface)}
      <div class="terminal-banner">
        <div><span>INVESTIGATION TERMINAL</span><strong>${escapeHtml(surface.terminal)}</strong></div>
        <p>같은 합성 조건의 replay gate만 검증됨 · Agent 전체 안전 인증 아님</p>
      </div>
      <div class="world-comparison">
        ${renderWorld("보호 전", "MEASURED FAILURE", surface.before, "failed")}
        <div class="comparison-arrow" aria-hidden="true">→</div>
        ${renderWorld("보호 후", "VERIFIED REPLAY", surface.after, "verified")}
      </div>
      <div class="result-detail-grid">
        <section class="finding-dossier">
          <article><span>FIRST DIVERGENCE</span><p>${escapeHtml(surface.divergence)}</p></article>
          <article><span>PROPOSED MITIGATION</span><p>${escapeHtml(surface.patch)}</p></article>
        </section>
        <section class="gate-dossier" aria-labelledby="gateTitle">
          <h2 id="gateTitle">Replay gates</h2>
          <ul>${gates}</ul>
        </section>
      </div>
      <ol class="evidence-legend" aria-label="증거 강도">${evidence}</ol>
    </section>`;
}

function renderNonResult(surface) {
  const outcomes = surface.outcomes.map((outcome) => `
    <article class="outcome-specimen ${outcome.source === "fixture" ? "fixture" : ""}"
      data-axis="${escapeHtml(outcome.axis)}">
      <header>
        <span>${escapeHtml(outcome.axis.toUpperCase())}</span>
        <strong>${escapeHtml(outcome.code)}</strong>
      </header>
      <p>${escapeHtml(outcome.copy)}</p>
      <footer>
        <span>source=${escapeHtml(outcome.source)}</span>
        <span>${outcome.source === "fixture" ? "SUBMITTED TARGET · NOT ANALYZED" : "NO FINDING CREATED"}</span>
      </footer>
    </article>`).join("");

  return `
    <section class="review-surface non-result-surface" data-review-surface="r4">
      ${surfaceIntro(surface)}
      <div class="axis-guide">
        <span>INVESTIGATION · 합성 조사의 결론</span>
        <span>OPERATIONAL · 조사 밖 실행 상태</span>
      </div>
      <div class="outcome-matrix">${outcomes}</div>
      <p class="matrix-note">Review comparison only · 실제 run은 하나의 terminal state만 표시합니다.</p>
    </section>`;
}

export function renderD2Surface(surface) {
  switch (surface?.id) {
    case "r1": return renderEntry(surface);
    case "r2": return renderRunning(surface);
    case "r3": return renderResult(surface);
    case "r4": return renderNonResult(surface);
    default: throw new Error(`Unknown D2 review surface: ${surface?.id || "missing"}`);
  }
}
