// Page state and flow.
const factsCache = {};

async function togglePolicy(li) {
  const button = li.querySelector(".policy-row");
  const panel = li.querySelector(".facts");
  const opening = panel.hidden;

  panel.hidden = !opening;
  li.classList.toggle("open", opening);
  button.setAttribute("aria-expanded", String(opening));
  if (!opening) return;

  const code = li.dataset.code;
  if (!factsCache[code]) {
    panel.innerHTML = `<p class="empty">Loading facts…</p>`;
    try {
      factsCache[code] = (await API.facts(code)).facts;
    } catch (err) {
      panel.innerHTML = `<p class="empty">${UI.escape(err.message)}</p>`;
      return;
    }
  }
  UI.renderFacts(panel, factsCache[code]);
}

async function init() {
  try {
    const [health, policies] = await Promise.all([API.health(), API.policies()]);
    UI.renderMode(health);
    UI.renderPolicies(policies);
    UI.renderStoreDate(health.fact_store_built_at);
  } catch (err) {
    UI.renderError(err.message);
  }

  document.getElementById("policy-list").addEventListener("click", (e) => {
    const button = e.target.closest(".policy-row");
    if (button && !button.disabled) togglePolicy(button.closest("li"));
  });
}

document.addEventListener("DOMContentLoaded", init);