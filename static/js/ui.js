const UI = {
  escape(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  },

  renderMode(health) {
    const badge = document.getElementById("mode-badge");
    badge.className = `badge ${health.mode}`;
    badge.textContent = health.mode === "live" ? "Live" : "Demo mode";
  },

  renderPolicies(policies) {
    document.getElementById("policy-list").innerHTML = policies
      .map(p => `
        <li>
          <span>${UI.escape(p.name)} <span class="muted">· ${UI.escape(p.insurer)}</span></span>
          <span class="${p.available ? "ok" : "missing"}">${p.available ? "Found" : "Missing PDF"}</span>
        </li>`)
      .join("");
  },

  renderError(message) {
    const badge = document.getElementById("mode-badge");
    badge.className = "badge error";
    badge.textContent = "Offline";
    document.getElementById("policy-list").innerHTML =
      `<li><span class="missing">${UI.escape(message)}</span></li>`;
  },
};