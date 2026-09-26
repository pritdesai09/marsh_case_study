// All rendering happens here. Functions take data and update the page.
const UI = {
  escape(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
  },

  COVERAGE_LABELS: {
    base: "Included",
    optional: "Optional (extra premium)",
    add_on: "Add-on policy",
    plan_dependent: "Plan-dependent",
  },

  renderMode(health) {
    const badge = document.getElementById("mode-badge");
    badge.className = `badge ${health.mode}`;
    badge.textContent = health.mode === "live" ? "Live" : "Demo mode";
  },

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