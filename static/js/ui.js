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

  // ---------------------------------------------------------------- header
  renderMode(health) {
    const badge = document.getElementById("mode-badge");
    badge.className = `badge ${health.mode}`;
    badge.textContent = health.mode === "live" ? "Live" : "Demo mode";
  },

  // ---------------------------------------------------------------- company search
  renderSuggestions(items, activeIndex = -1) {
    const list = document.getElementById("suggestions");
    if (!items.length) {
      list.hidden = true;
      list.innerHTML = "";
      return;
    }
    list.innerHTML = items
      .map((c, i) => `
        <li role="option" data-index="${i}" class="${i === activeIndex ? "active" : ""}">
          ${UI.escape(c.label)}
          <span class="desc">${UI.escape(c.description || "No description")}</span>
        </li>`)
      .join("");
    list.hidden = false;
  },

  setCompanyError(message) {
    const el = document.getElementById("company-error");
    el.textContent = message || "";
    el.hidden = !message;
  },

  setResearching(busy) {
    document.getElementById("research-btn").disabled = busy;
    document.getElementById("company-input").disabled = busy;
    UI.show("profile-loading", busy);
    if (busy) UI.show("profile-result", false);
  },

  // ---------------------------------------------------------------- profile
  basisBadge(field) {
    if (field.basis === "sourced") {
      return `<a class="basis sourced" href="${UI.escape(field.source_url)}" target="_blank" rel="noopener"
                 title="Open the source">Sourced · ${UI.escape(field.source)} ↗</a>`;
    }
    return `<span class="basis assumption" title="${UI.escape(field.reason || "Estimated")}">Assumption</span>`;
  },

  renderProfile(profile) {
    const fields = profile.fields || {};
    const tiles = Object.keys(UI.FIELD_LABELS)
      .filter(key => fields[key])
      .map(key => {
        const f = fields[key];
        const value = key === "website"
          ? `<a href="${UI.escape(f.display)}" target="_blank" rel="noopener">${UI.escape(f.display.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""))}</a>`
          : UI.escape(f.display);
        const reason = f.basis === "assumption" && f.reason ? `<div class="reason">${UI.escape(f.reason)}</div>` : "";
        return `
          <div class="fact-tile">
            <div class="label">${UI.FIELD_LABELS[key]}</div>
            <div class="value">${value}</div>
            ${UI.basisBadge(f)}${reason}
          </div>`;
      }).join("");

    const warnings = (profile.warnings || [])
      .map(w => `<div class="banner">⚠ ${UI.escape(w)}</div>`).join("");

    const others = (profile.candidates || []).filter(c => c.id !== profile.wikidata_id && c.is_company);
    const alternatives = others.length ? `
      <div class="alternatives">
        <span class="muted">Not the right company?</span>
        ${others.map(c => `<button type="button" class="btn chip" data-wikidata-id="${UI.escape(c.id)}"
            title="${UI.escape(c.description)}">${UI.escape(c.label)}</button>`).join("")}
      </div>` : "";

    const summary = fields.summary ? `
      <div class="section-label">Summary</div>
      <p class="summary">${UI.escape(fields.summary.display)} ${UI.basisBadge(fields.summary)}</p>` : "";

    const wf = profile.workforce_profile;
    const risks = (profile.risks || []).map(r => `
      <li>
        <span class="pill ${r.relevance}">${r.relevance === "high" ? "High" : "Medium"}</span>
        <div>
          <strong>${UI.escape(r.label)}</strong>
          <div class="reason">${UI.escape(r.rationale)}</div>
        </div>
      </li>`).join("");

    const source = profile.generated_by === "rules" ? "industry rules" : `AI (${UI.escape(profile.generated_by)})`;
    const el = document.getElementById("profile-result");
    el.innerHTML = `
      <div class="profile">
        <div class="profile-head">
          <div>
            <h3>${UI.escape(profile.resolved_name)}</h3>
            <div class="muted">${UI.escape(profile.description || "No verified description")}</div>
          </div>
          ${profile.wikidata_url ? `<a href="${UI.escape(profile.wikidata_url)}" target="_blank" rel="noopener">View on Wikidata ↗</a>` : ""}
        </div>
        ${warnings}
        ${alternatives}

        ${tiles ? `<div class="section-label">Verified facts</div><div class="fact-grid">${tiles}</div>` : ""}
        ${summary}

        <div class="section-label">Likely workforce <span class="basis assumption">Assumption</span></div>
        <p class="workforce">${UI.escape(wf.value)}<span class="reason"> Based on: ${UI.escape(wf.reason)}</span></p>

        <div class="section-label">Key health-insurance risks <span class="basis assumption">Assumption</span></div>
        <ul class="risk-list">${risks}</ul>

        <div class="profile-foot">Assumptions by ${source} · ${new Date(profile.generated_at).toLocaleString()}</div>
      </div>`;
    el.hidden = false;
  },

  // ---------------------------------------------------------------- knowledge base
  renderStoreDate(generatedAt) {
    document.getElementById("store-date").textContent = generatedAt
      ? `Fact store built ${new Date(generatedAt).toLocaleString()}`
      : "";
  },

  policyStatus(p) {
    if (!p.available) return `<span class="pill missing">Missing PDF</span>`;
    if (!p.facts) return `<span class="pill review">No facts yet</span>`;
    const review = p.needs_review
      ? `<span class="pill review">${p.needs_review} to review</span>` : "";
    return `<span class="pill ok">${p.verified} verified</span>${review}`;
  },

  renderPolicies(policies) {
    document.getElementById("policy-list").innerHTML = policies
      .map(p => `
        <li data-code="${UI.escape(p.code)}">
          <button class="policy-row" ${p.facts ? "" : "disabled"} aria-expanded="false">
            <span>
              <span class="name">${UI.escape(p.name)}</span>
              <span class="muted"> · ${UI.escape(p.insurer)}</span>
            </span>
            <span class="meta">
              ${UI.policyStatus(p)}
              ${p.facts ? `<span class="chevron">▶</span>` : ""}
            </span>
          </button>
          <div class="facts" hidden></div>
        </li>`)
      .join("");
  },

  renderFacts(container, facts) {
    if (!facts.length) {
      container.innerHTML = `<p class="empty">No facts for this policy yet.</p>`;
      return;
    }
    // Facts that need a human look go first
    const order = { needs_review: 0, auto_verified: 1, human_verified: 2 };
    const sorted = [...facts].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
    const rows = sorted.map(f => {
      const statusPill = f.status === "needs_review"
        ? `<span class="pill review">Review</span>`
        : `<span class="pill ok">${f.status === "human_verified" ? "Human-checked" : "Verified"}</span>`;
      const flags = f.flags?.length
        ? `<div class="flags">⚠ ${UI.escape(f.flags.join("; "))}</div>` : "";
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
        <thead><tr><th>Benefit</th><th>Value</th><th>Coverage</th><th>Source</th><th>Status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  },

  renderError(message) {
    const badge = document.getElementById("mode-badge");
    badge.className = "badge error";
    badge.textContent = "Offline";
    document.getElementById("policy-list").innerHTML =
      `<li><p class="empty">${UI.escape(message)}</p></li>`;
  },
};