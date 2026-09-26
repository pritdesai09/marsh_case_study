// All rendering happens here. Functions take data and update the page.
const UI = {
  escape(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
  },

  show(id, visible = true) {
    document.getElementById(id).hidden = !visible;
  },

  setError(id, message) {
    const el = document.getElementById(id);
    el.textContent = message || "";
    el.hidden = !message;
  },

  COVERAGE_LABELS: {
    base: "Included",
    optional: "Optional (extra premium)",
    add_on: "Add-on policy",
    plan_dependent: "Plan-dependent",
  },

  FIELD_LABELS: {
    industry: "Industry",
    employees: "Employees",
    headquarters: "Headquarters",
    country: "Country",
    founded: "Founded",
    revenue: "Revenue",
    website: "Website",
  },

  // How a claim looks, from its audit status + what the advisor did
  VISUAL: {
    rejected: { cls: "rejected", label: "Rejected" },
    approved: { cls: "approved", label: "Approved" },
    must_fix: { cls: "fail", label: "Fail" },
    needs_approval: { cls: "review", label: "Review" },
    verified: { cls: "verified", label: "Verified" },
    info: { cls: "info", label: "Marsh" },
  },

  visual(entry, state) {
    if (state === "ok") return UI.VISUAL[entry.status === "info" ? "info" : "verified"];
    return UI.VISUAL[state] || UI.VISUAL.verified;
  },

  CHECK_ICON: { pass: "✓", review: "!", fail: "✗" },

  // ---------------------------------------------------------------- header
  renderMode(health) {
    const badge = document.getElementById("mode-badge");
    badge.className = `badge ${health.mode}`;
    badge.textContent = health.mode === "live" ? "Live" : "Demo mode";
    badge.title = health.mode === "live" ? `AI: ${health.model}` : "No Gemini key: templates and rule checks only";
  },

  renderOffline(message) {
    const badge = document.getElementById("mode-badge");
    badge.className = "badge error";
    badge.textContent = "Offline";
    document.getElementById("policy-picks").innerHTML = `<p class="empty">${UI.escape(message)}</p>`;
  },

  // ---------------------------------------------------------------- company search
  renderSuggestions(items, activeIndex = -1) {
    const list = document.getElementById("suggestions");
    if (!items.length) {
      list.hidden = true;
      list.innerHTML = "";
      return;
    }
    list.innerHTML = items.map((c, i) => `
        <li role="option" data-index="${i}" class="${i === activeIndex ? "active" : ""}">
          ${UI.escape(c.label)}
          <span class="desc">${UI.escape(c.description || "No description")}</span>
        </li>`).join("");
    list.hidden = false;
  },

  // ---------------------------------------------------------------- policy picks
  MAX_POLICIES: 4,

  renderPolicyPicks(policies, checked) {
    const shown = policies.filter(p => p.available && (p.facts > 0 || p.uploaded));
    const el = document.getElementById("policy-picks");
    if (!shown.length) {
      el.innerHTML = `<p class="empty">No policy facts yet. Build the fact store first.</p>`;
      return;
    }
    el.innerHTML = shown.map(p => {
      const ready = p.status === "ready" && p.verified > 0;
      let meta = `${p.verified} verified facts`;
      if (p.status === "processing") meta = `<span class="spinner small"></span> Reading the brochure…`;
      else if (p.status === "failed") meta = `<span class="bad">Failed: ${UI.escape(p.error || "unknown error")}</span>`;
      return `
        <label class="pick ${ready ? "" : "disabled"}" title="${UI.escape(p.insurer)}">
          <input type="checkbox" name="policy" value="${UI.escape(p.code)}"
                 ${ready && checked.has(p.code) ? "checked" : ""} ${ready ? "" : "disabled"}>
          <span>${UI.escape(p.name)}${p.uploaded ? ` <span class="tag">Uploaded</span>` : ""}
            <span class="pick-meta">${meta}</span></span>
        </label>`;
    }).join("");
    UI.enforcePolicyLimit();
  },

  selectedPolicies() {
    return [...document.querySelectorAll('#policy-picks input[name="policy"]:checked')].map(i => i.value);
  },

  enforcePolicyLimit() {
    const full = UI.selectedPolicies().length >= UI.MAX_POLICIES;
    document.querySelectorAll('#policy-picks label.pick:not(.disabled) input').forEach(input => {
      input.disabled = full && !input.checked;
      input.closest("label").classList.toggle("limit", full && !input.checked);
    });
  },

  // ---------------------------------------------------------------- progress
  setBusy(busy) {
    document.getElementById("generate-btn").disabled = busy;
    document.getElementById("generate-btn").textContent = busy ? "Working…" : "Generate pitch";
    document.getElementById("company-input").disabled = busy;
    document.querySelectorAll("#policy-picks input").forEach(i => { if (busy) i.disabled = true; });
    if (!busy) UI.enforcePolicyLimit();
  },

  // state: "active" | "done" | "error" | "" for each step
  setProgress(steps) {
    const el = document.getElementById("progress");
    el.hidden = false;
    el.querySelectorAll("li").forEach(li => {
      li.className = steps[li.dataset.step] || "";
    });
  },

  // ---------------------------------------------------------------- result header
  renderHead(pitch, report) {
    const s = report.summary;
    const rs = report.review_summary;
    const best = pitch.ranking.find(p => p.code === pitch.recommended);
    const others = pitch.ranking.filter(p => p.code !== pitch.recommended).map(p => p.name);
    const warnings = [...(pitch.profile.warnings || []), ...(pitch.warnings || [])]
      .map(w => `<div class="banner">⚠ ${UI.escape(w)}</div>`).join("");
    const c = s.counts;
    const initial = report.initial_summary
      ? `<span class="muted">First audit: ${report.initial_summary.overall} (${report.initial_summary.grounding_score}%)</span>` : "";
    const done = rs.counts;
    const advisor = [done.approved ? `${done.approved} approved` : "", done.rejected ? `${done.rejected} rejected` : "",
      rs.edited.length ? `${rs.edited.length} edited` : ""].filter(Boolean).join(" · ");
    // Once everything is resolved, the badge shows the sign-off rather than the raw machine verdict
    const verdict = rs.ready
      ? { cls: "pass", label: "Advisor sign-off", word: "READY" }
      : { cls: s.overall.toLowerCase(), label: "Audit", word: s.overall };

    document.getElementById("result-head").innerHTML = `
      <div class="head-main">
        <div>
          <div class="eyebrow">Pitch for</div>
          <h2 class="company">${UI.escape(pitch.company)}</h2>
          <div class="muted">Recommended: <b class="reco-name">${UI.escape(best.name)}</b> · fit ${best.score}/100
            ${others.length ? ` · compared with ${others.map(UI.escape).join(", ")}` : ""}</div>
        </div>
        <div class="verdict v-${verdict.cls}" title="${UI.escape(s.headline)}">
          <span class="verdict-label">${verdict.label}</span>
          <span class="verdict-word">${verdict.word}</span>
          <span class="verdict-score">${s.grounding_score}% grounded</span>
        </div>
      </div>
      <div class="head-counts">
        <span class="count verified">${c.verified} verified</span>
        <span class="count review">${c.review} review</span>
        <span class="count fail">${c.fail} fail</span>
        <span class="count info">${c.info} Marsh messaging</span>
        ${advisor ? `<span class="count advisor">Advisor: ${advisor}</span>` : ""}
        ${initial}
      </div>
      ${warnings}
      <div class="head-actions">
        <div class="gate ${rs.ready ? "ready" : "blocked"}">
          <span class="gate-icon">${rs.ready ? "✓" : "🔒"}</span>
          <span>${UI.escape(rs.message)}</span>
          ${rs.ready ? "" : `<button type="button" class="btn link" data-action="next-issue">Show next →</button>`}
        </div>
        <div class="head-buttons">
          <details class="menu">
            <summary class="btn">Audit report ▾</summary>
            <div class="menu-list">
              <a href="${API.reportUrl(report.audit_id, "html")}" target="_blank" rel="noopener">Open report (HTML)</a>
              <a href="${API.reportUrl(report.audit_id, "csv")}" download>Download CSV</a>
              <a href="${API.reportUrl(report.audit_id, "json")}" download>Download JSON</a>
            </div>
          </details>
          <button id="download-btn" type="button" class="btn primary" ${rs.ready ? "" : "disabled"}
            title="${rs.ready ? "Download the approved deck" : UI.escape(rs.message)}">Download PowerPoint</button>
        </div>
      </div>`;
  },

  // ---------------------------------------------------------------- slides
  // One statement on a slide. Clicking it opens the inspector.
  claimHTML(pitchClaim, ctx, { compact = false } = {}) {
    const entry = ctx.claims[pitchClaim.id];
    if (!entry) return `<div class="claim">${UI.escape(pitchClaim.text)}</div>`;
    const state = ctx.states[entry.id];
    const v = UI.visual(entry, state);
    const text = entry.edited ? entry.text : (compact && pitchClaim.short_text) || entry.text;
    const selected = ctx.selected === entry.id ? "selected" : "";
    return `<button type="button" class="claim s-${v.cls} ${selected}" data-claim-id="${UI.escape(entry.id)}"
              title="${UI.escape(entry.reason)}">
              <span class="claim-text">${UI.escape(text)}</span>
              <span class="chip c-${v.cls}">${v.label}${entry.edited ? " · edited" : ""}</span>
            </button>`;
  },

  renderSlide(s, ctx) {
    const claim = (c, opts) => UI.claimHTML(c, ctx, opts);
    let body = "";
    if (s.type === "overview") {
      const tiles = Object.entries(s.facts || {}).map(([k, v]) =>
        `<div><b>${UI.escape(UI.FIELD_LABELS[k] || k)}</b>${UI.escape(v)}</div>`).join("");
      body = `${tiles ? `<div class="mini-tiles">${tiles}</div>` : ""}${s.claims.map(c => claim(c)).join("")}`;
    } else if (s.type === "why_marsh") {
      body = `<div class="marsh-grid">${s.claims.map(c => claim(c)).join("")}</div>`;
    } else if (s.type === "risk_benefits") {
      body = s.rows.length ? s.rows.map(r => `
        <div class="risk-row">
          <div class="risk-tag ${r.relevance}">${UI.escape(r.risk.split("(")[0])}</div>
          <div>${claim(r.claim)}</div>
        </div>`).join("") : `<p class="empty">No verified benefits matched these risks.</p>`;
    } else if (s.type === "comparison") {
      const t = s.table;
      body = `<div class="compare"><table>
        <thead><tr><th></th>${t.columns.map(c => `<th>${c.code === ctx.recommended ? "★ " : ""}${UI.escape(c.name)}</th>`).join("")}</tr></thead>
        <tbody>
          <tr class="score"><td>Fit score</td>${t.columns.map(c => `<td>${c.score} / 100</td>`).join("")}</tr>
          ${t.rows.map(r => `<tr><td><strong>${UI.escape(r.label)}</strong></td>${r.cells.map(c =>
            c.claim_type === "none" ? `<td class="none">Not stated in brochure</td>` : `<td>${claim(c, { compact: true })}</td>`).join("")}</tr>`).join("")}
        </tbody></table></div>`;
    } else if (s.type === "recommendation") {
      body = `<div class="reco">
          <div class="reco-score">Fit score<b>${s.score}</b>out of 100</div>
          <div>${s.claims.map(c => claim(c)).join("")}</div>
        </div>
        <div class="disclaimer">${UI.escape(s.disclaimer)}</div>`;
    }
    return `<article class="slide" id="slide-${s.n}">
        <div class="slide-head"><h4>${UI.escape(s.title)}</h4><span>Slide ${s.n} / ${ctx.total}</span></div>
        <div class="slide-body">${body}</div>
      </article>`;
  },

  renderSlides(pitch, ctx) {
    document.getElementById("slides").innerHTML = pitch.slides.map(s => UI.renderSlide(s, ctx)).join("");
  },

  // ---------------------------------------------------------------- inspector
  evidenceHTML(e) {
    if (e.kind === "fact") {
      return `<div class="ev">
          <div class="ev-src">${UI.escape(e.source)} <span class="muted">· ${UI.escape(e.fact_id)}</span></div>
          <div><b>${UI.escape(e.benefit)}</b>: ${UI.escape(e.value)}
            <span class="pill ${UI.escape(e.coverage_type)}">${UI.escape(UI.COVERAGE_LABELS[e.coverage_type] || e.coverage_type)}</span></div>
          ${e.conditions ? `<div class="muted">${UI.escape(e.conditions)}</div>` : ""}
          <blockquote>“${UI.escape(e.quote)}”</blockquote>
        </div>`;
    }
    if (e.kind === "retrieved") {
      return `<div class="ev">
          <div class="ev-src">${UI.escape(e.source)} <span class="tag">found by search, not cited</span></div>
          <blockquote>“${UI.escape(e.quote)}”</blockquote>
        </div>`;
    }
    if (e.kind === "company") {
      const src = e.url ? `<a href="${UI.escape(e.url)}" target="_blank" rel="noopener">${UI.escape(e.source)} ↗</a>` : UI.escape(e.source);
      return `<div class="ev">
          <div class="ev-src">${src} ${e.basis === "sourced" ? "" : `<span class="tag">assumption</span>`}</div>
          <div><b>${UI.escape(UI.FIELD_LABELS[e.field] || e.field)}</b>: ${UI.escape(e.quote)}</div>
        </div>`;
    }
    return `<div class="ev"><div class="ev-src">${UI.escape(e.source)}</div></div>`;
  },

  renderInspector(entry, ctx, { editing = false, busy = false, draft = null } = {}) {
    const el = document.getElementById("inspector");
    const openCount = ctx.blockers.length;
    if (!entry) {
      el.innerHTML = `
        <h3>Check a statement</h3>
        <p class="muted">Click any statement on the slides to see the policy clause it was traced to, the checks it passed,
          and to approve, edit or reject it.</p>
        ${openCount ? `<p class="insp-todo">${openCount} statement${openCount > 1 ? "s" : ""} need${openCount > 1 ? "" : "s"} your decision.</p>
          <button type="button" class="btn primary" data-action="next-issue">Show the first one</button>`
          : `<p class="insp-done">✓ Nothing needs your attention.</p>`}
        <div class="legend">
          <span class="chip c-verified">Verified</span> traced to a clause, all checks passed<br>
          <span class="chip c-review">Review</span> traceable, needs a human judgement<br>
          <span class="chip c-fail">Fail</span> not supported: edit or reject<br>
          <span class="chip c-info">Marsh</span> Marsh messaging, outside the brochures
        </div>`;
      el.classList.remove("open");
      return;
    }
    const state = ctx.states[entry.id];
    const v = UI.visual(entry, state);
    const checks = entry.checks.map(k => `
      <li class="${k.result}"><span class="ci">${UI.CHECK_ICON[k.result] || "•"}</span>
        <span><b>${UI.escape(k.name)}</b> ${UI.escape(k.detail)}</span></li>`).join("");
    const evidence = entry.evidence.length ? entry.evidence.map(UI.evidenceHTML).join("")
      : `<p class="muted">No supporting passage was found in the selected brochures.</p>`;

    const b = (action, label, cls = "") =>
      `<button type="button" class="btn ${cls}" data-action="${action}" ${busy ? "disabled" : ""}>${label}</button>`;
    let actions = "";
    if (state === "must_fix") actions = b("edit", "Edit", "primary") + b("reject", "Reject");
    else if (state === "needs_approval") actions = b("approve", "Approve", "primary") + b("edit", "Edit") + b("reject", "Reject");
    else if (state === "approved" || state === "rejected") actions = b("reset", "Undo decision") + (state === "approved" ? b("edit", "Edit") : "");
    else actions = b("edit", "Edit") + b("reject", "Reject");
    if (entry.edited) actions += b("revert", "Restore original");

    const hint = {
      must_fix: "A failed statement can't be approved. Edit it to match the clause (it is re-audited instantly) or reject it to drop it from the deck.",
      needs_approval: "Read the clause below. Approve if the statement is fair, otherwise edit or reject it.",
      approved: "You approved this statement.",
      rejected: "Rejected: it will be left out of the deck.",
    }[state] || "";

    el.innerHTML = `
      <div class="insp-head">
        <span class="insp-id">${UI.escape(entry.id)} · Slide ${entry.slide}</span>
        <span class="chip c-${v.cls}">${v.label}</span>
      </div>
      <p class="insp-text ${state === "rejected" ? "struck" : ""}">${UI.escape(entry.text)}</p>
      ${entry.edited ? `<p class="insp-original">Original: ${UI.escape(entry.original_text)}</p>` : ""}
      <div class="insp-reason r-${v.cls}">${UI.escape(entry.reason)}</div>
      ${hint ? `<p class="insp-hint">${hint}</p>` : ""}

      <div class="insp-actions" ${editing ? "hidden" : ""}>${actions}</div>
      <div class="insp-edit" ${editing ? "" : "hidden"}>
        <label for="edit-text" class="field-label">New wording</label>
        <textarea id="edit-text" rows="4" maxlength="600" ${busy ? "disabled" : ""}>${UI.escape(draft ?? entry.text)}</textarea>
        <p class="muted small-text">Keep amounts, limits and qualifiers (optional, add-on, on select plans) exactly as the clause states.</p>
        <div class="insp-actions">
          <button type="button" class="btn primary" data-action="save-edit" ${busy ? "disabled" : ""}>${busy ? "Re-auditing…" : "Save and re-audit"}</button>
          <button type="button" class="btn" data-action="cancel-edit" ${busy ? "disabled" : ""}>Cancel</button>
        </div>
      </div>
      <p id="insp-error" class="error" hidden></p>

      <div class="section-label">Checks</div>
      <ul class="checks">${checks || `<li class="pass"><span class="ci">•</span><span>No checks apply to Marsh messaging; confirm the wording with Marsh marketing.</span></li>`}</ul>
      <div class="section-label">Traced to</div>
      ${evidence}
      <div class="insp-foot">
        <button type="button" class="btn link" data-action="close-inspector">Close</button>
        ${openCount ? `<button type="button" class="btn link" data-action="next-issue">Next to review →</button>` : ""}
      </div>`;
    el.classList.add("open");
  },

  // ---------------------------------------------------------------- audit tab
  renderAuditTab(report, filter = "all") {
    const s = report.summary;
    const states = report.review_summary.states;
    const rows = report.claims.filter(c => filter === "all" || c.status === filter);
    const count = (k) => report.claims.filter(c => c.status === k).length;
    const chip = (k, label) =>
      `<button type="button" class="btn chip ${filter === k ? "on" : ""}" data-filter="${k}">${label} (${k === "all" ? report.claims.length : count(k)})</button>`;
    const advisor = { approved: "Approved", rejected: "Rejected", must_fix: "To fix", needs_approval: "To approve", ok: "" };
    document.getElementById("tab-audit").innerHTML = `
      <div class="card-head">
        <div>
          <h3>Audit: ${s.overall} · ${s.grounding_score}% grounded</h3>
          <p class="muted">${UI.escape(s.headline)} Each statement was traced to a brochure clause and checked for
            citation, numbers, coverage qualifiers and insurer, then read by an AI auditor (${UI.escape(report.auditor)}).</p>
          <p class="muted small-text">Policy documents: ${report.policies_audited.map(p => UI.escape(p.name)).join(", ")}</p>
        </div>
        <a class="btn" href="${API.reportUrl(report.audit_id, "html")}" target="_blank" rel="noopener">Open printable report</a>
      </div>
      <div class="filters">${chip("all", "All")}${chip("fail", "Fail")}${chip("review", "Review")}${chip("verified", "Verified")}${chip("info", "Marsh")}</div>
      <div class="audit-table"><table>
        <thead><tr><th>Claim</th><th>Status</th><th>Statement</th><th>Why</th><th>Traced to</th><th>Advisor</th></tr></thead>
        <tbody>${rows.map(c => `
          <tr data-claim-id="${UI.escape(c.id)}" tabindex="0">
            <td class="nowrap">${UI.escape(c.id)}</td>
            <td><span class="chip c-${c.status === "info" ? "info" : c.status}">${c.status === "info" ? "Marsh" : c.status}</span></td>
            <td>${UI.escape(c.text)}${c.edited ? ` <span class="tag">edited</span>` : ""}</td>
            <td class="muted">${UI.escape(c.reason)}</td>
            <td class="muted">${UI.escape([...new Set(c.evidence.map(e => e.source))].join("; ") || "—")}</td>
            <td>${advisor[states[c.id]] || ""}</td>
          </tr>`).join("") || `<tr><td colspan="6" class="empty">Nothing in this group.</td></tr>`}
        </tbody></table></div>
      <div class="section-label">How the advisor uses this report</div>
      <ul class="guidance">${report.advisor_guidance.map(g => `<li>${UI.escape(g)}</li>`).join("")}</ul>`;
  },

  // ---------------------------------------------------------------- profile tab
  basisBadge(field) {
    if (field.basis === "sourced") {
      return `<a class="basis sourced" href="${UI.escape(field.source_url)}" target="_blank" rel="noopener"
                 title="Open the source">Sourced · ${UI.escape(field.source)} ↗</a>`;
    }
    return `<span class="basis assumption" title="${UI.escape(field.reason || "Estimated")}">Assumption</span>`;
  },

  renderProfile(profile) {
    const fields = profile.fields || {};
    const tiles = Object.keys(UI.FIELD_LABELS).filter(key => fields[key]).map(key => {
      const f = fields[key];
      const value = key === "website"
        ? `<a href="${UI.escape(f.display)}" target="_blank" rel="noopener">${UI.escape(f.display.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""))}</a>`
        : UI.escape(f.display);
      const reason = f.basis === "assumption" && f.reason ? `<div class="reason">${UI.escape(f.reason)}</div>` : "";
      return `<div class="fact-tile"><div class="label">${UI.FIELD_LABELS[key]}</div>
          <div class="value">${value}</div>${UI.basisBadge(f)}${reason}</div>`;
    }).join("");

    const others = (profile.candidates || []).filter(c => c.id !== profile.wikidata_id && c.is_company);
    const alternatives = others.length ? `
      <div class="alternatives">
        <span class="muted">Not the right company? Regenerate for:</span>
        ${others.map(c => `<button type="button" class="btn chip" data-wikidata-id="${UI.escape(c.id)}"
            data-label="${UI.escape(c.label)}" title="${UI.escape(c.description)}">${UI.escape(c.label)}</button>`).join("")}
      </div>` : "";
    const summary = fields.summary ? `
      <div class="section-label">Summary</div>
      <p class="summary">${UI.escape(fields.summary.display)} ${UI.basisBadge(fields.summary)}</p>` : "";
    const wf = profile.workforce_profile;
    const risks = (profile.risks || []).map(r => `
      <li><span class="pill ${r.relevance}">${r.relevance === "high" ? "High" : "Medium"}</span>
        <div><strong>${UI.escape(r.label)}</strong><div class="reason">${UI.escape(r.rationale)}</div></div></li>`).join("");
    const source = profile.generated_by === "rules" ? "industry rules" : `AI (${UI.escape(profile.generated_by)})`;

    document.getElementById("tab-profile").innerHTML = `
      <div class="profile-head">
        <div>
          <h3>${UI.escape(profile.resolved_name)}</h3>
          <div class="muted">${UI.escape(profile.description || "No verified description")}</div>
        </div>
        ${profile.wikidata_url ? `<a href="${UI.escape(profile.wikidata_url)}" target="_blank" rel="noopener">View on Wikidata ↗</a>` : ""}
      </div>
      ${(profile.warnings || []).map(w => `<div class="banner">⚠ ${UI.escape(w)}</div>`).join("")}
      ${alternatives}
      ${tiles ? `<div class="section-label">Verified facts</div><div class="fact-grid">${tiles}</div>` : ""}
      ${summary}
      <div class="section-label">Likely workforce <span class="basis assumption">Assumption</span></div>
      <p class="workforce">${UI.escape(wf.value)}<span class="reason"> Based on: ${UI.escape(wf.reason)}</span></p>
      <div class="section-label">Key health-insurance risks <span class="basis assumption">Assumption</span></div>
      <ul class="risk-list">${risks}</ul>
      <div class="profile-foot">Assumptions by ${source} · ${new Date(profile.generated_at).toLocaleString()}</div>`;
  },

  // ---------------------------------------------------------------- ranking tab
  renderRanking(pitch) {
    const rows = pitch.ranking.map((p, i) => {
      const detail = p.breakdown.map(b => `
        <tr>
          <td>${UI.escape(b.risk.split("(")[0])} <span class="pill ${b.relevance === "high" ? "missing" : "review"}">${b.relevance}</span></td>
          <td>${b.facts.length
            ? b.facts.map(f => `${UI.escape(f.benefit)} <span class="pill ${UI.escape(f.coverage_type)}">${UI.escape(UI.COVERAGE_LABELS[f.coverage_type] || f.coverage_type)}</span>`).join("<br>")
            : `<span class="gap">No matching benefit in the brochure</span>`}</td>
          <td class="num">${b.score}</td>
        </tr>`).join("");
      return `
        <details class="rank-row ${i === 0 ? "best" : ""}" ${i === 0 ? "open" : ""}>
          <summary>
            <span class="rank-name">${UI.escape(p.name)}${i === 0 ? `<span class="pill ok">Recommended</span>` : ""}</span>
            <span class="bar"><span style="width:${p.score}%"></span></span>
            <span class="rank-score">${p.score}</span>
          </summary>
          <div class="rank-detail"><table>
            <thead><tr><th>Client risk</th><th>Matching brochure benefits</th><th class="num">Points</th></tr></thead>
            <tbody>${detail}</tbody></table></div>
        </details>`;
    }).join("");
    document.getElementById("tab-ranking").innerHTML = `
      <h3>Why ${UI.escape(pitch.ranking[0].name)}</h3>
      <p class="muted">Each policy is scored by code, not by the AI: every client risk is matched to verified brochure benefits.
        Included benefits count fully; plan-dependent, optional and add-on benefits count less. Click a policy to see its working.</p>
      <div class="ranking">${rows}</div>`;
  },

  // ---------------------------------------------------------------- tabs
  selectTab(name) {
    document.querySelectorAll(".tabs [role=tab]").forEach(t => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
    ["slides", "audit", "profile", "ranking"].forEach(t => UI.show(`tab-${t}`, t === name));
  },

  // ---------------------------------------------------------------- policy library
  renderStoreDate(generatedAt) {
    document.getElementById("store-date").textContent = generatedAt
      ? `Fact store updated ${new Date(generatedAt).toLocaleString()}` : "";
  },

  policyStatus(p) {
    if (p.status === "processing") return `<span class="pill review">Processing…</span>`;
    if (p.status === "failed") return `<span class="pill missing" title="${UI.escape(p.error || "")}">Failed</span>`;
    if (!p.available) return `<span class="pill missing">Missing PDF</span>`;
    if (!p.facts) return `<span class="pill review">No facts yet</span>`;
    const review = p.needs_review ? `<span class="pill review">${p.needs_review} to review</span>` : "";
    return `<span class="pill ok">${p.verified} verified</span>${review}`;
  },

  renderPolicies(policies) {
    document.getElementById("policy-list").innerHTML = policies.map(p => `
        <li data-code="${UI.escape(p.code)}">
          <div class="policy-line">
            <button class="policy-row" ${p.facts ? "" : "disabled"} aria-expanded="false">
              <span><span class="name">${UI.escape(p.name)}</span>
                <span class="muted"> · ${UI.escape(p.insurer)}</span>${p.uploaded ? ` <span class="tag">Uploaded</span>` : ""}</span>
              <span class="meta">${UI.policyStatus(p)}${p.facts ? `<span class="chevron">▶</span>` : ""}</span>
            </button>
            ${p.uploaded && p.status !== "processing" ? `<button type="button" class="btn link danger" data-remove="${UI.escape(p.code)}">Remove</button>` : ""}
          </div>
          <div class="facts" hidden></div>
        </li>`).join("");
  },

  renderFacts(container, facts) {
    if (!facts.length) {
      container.innerHTML = `<p class="empty">No facts for this policy yet.</p>`;
      return;
    }
    const order = { needs_review: 0, auto_verified: 1, human_verified: 2 };
    const sorted = [...facts].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
    const rows = sorted.map(f => {
      const statusPill = f.status === "needs_review"
        ? `<span class="pill review">Not used</span>`
        : `<span class="pill ok">${f.status === "human_verified" ? "Human-checked" : "Verified"}</span>`;
      const flags = f.flags?.length ? `<div class="flags">⚠ ${UI.escape(f.flags.join("; "))}</div>` : "";
      return `
        <tr>
          <td><strong>${UI.escape(f.benefit)}</strong>${flags}</td>
          <td>${UI.escape(f.value)}${f.conditions ? `<div class="muted">${UI.escape(f.conditions)}</div>` : ""}</td>
          <td><span class="pill ${UI.escape(f.coverage_type)}">${UI.escape(UI.COVERAGE_LABELS[f.coverage_type] || f.coverage_type)}</span></td>
          <td class="quote">“${UI.escape(f.quote)}” <span class="muted">p.${f.page}</span></td>
          <td>${statusPill}</td>
        </tr>`;
    }).join("");
    container.innerHTML = `
      <table>
        <thead><tr><th>Benefit</th><th>Value</th><th>Coverage</th><th>Brochure quote</th><th>Status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  },
};