// Page state and flow.
const state = {
  profile: null,        // the researched company profile
  pitch: null,          // the generated pitch
  suggestions: [],      // current autocomplete options
  activeSuggestion: -1, // keyboard-highlighted option
  pickedId: null,       // Wikidata id chosen from the suggestions, if any
  searchTimer: null,
  searchSeq: 0,         // ignore out-of-order search responses
};
const factsCache = {};

// ---------------------------------------------------------------- company search box
function closeSuggestions() {
  clearTimeout(state.searchTimer);  // cancel a search that hasn't started yet
  state.searchSeq++;                // and ignore one that is already in flight
  state.suggestions = [];
  state.activeSuggestion = -1;
  UI.renderSuggestions([]);
}

function onCompanyInput(e) {
  state.pickedId = null;  // typing again means the earlier pick no longer applies
  UI.setCompanyError("");
  clearTimeout(state.searchTimer);
  const q = e.target.value.trim();
  if (q.length < 2) return closeSuggestions();

  state.searchTimer = setTimeout(async () => {
    const seq = ++state.searchSeq;
    try {
      const items = await API.searchCompanies(q);
      const input = document.getElementById("company-input");
      if (seq !== state.searchSeq || document.activeElement !== input) return;  // stale or no longer typing
      state.suggestions = items;
      state.activeSuggestion = -1;
      UI.renderSuggestions(items);
    } catch {
      closeSuggestions();  // suggestions are optional; the Research button still works
    }
  }, 300);
}

function pickSuggestion(index) {
  const item = state.suggestions[index];
  if (!item) return;
  document.getElementById("company-input").value = item.label;
  state.pickedId = item.id;
  closeSuggestions();
}

function onCompanyKeydown(e) {
  const count = state.suggestions.length;
  if (!count) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const step = e.key === "ArrowDown" ? 1 : -1;
    state.activeSuggestion = (state.activeSuggestion + step + count) % count;
    UI.renderSuggestions(state.suggestions, state.activeSuggestion);
  } else if (e.key === "Enter" && state.activeSuggestion >= 0) {
    e.preventDefault();
    pickSuggestion(state.activeSuggestion);
  } else if (e.key === "Escape") {
    closeSuggestions();
  }
}

// ---------------------------------------------------------------- research
async function researchCompany(company, wikidataId = null) {
  const name = company.trim();
  if (!name) {
    UI.setCompanyError("Please enter a company name.");
    document.getElementById("company-input").focus();
    return;
  }
  closeSuggestions();
  UI.setCompanyError("");
  UI.setResearching(true);
  try {
    state.profile = await API.profile(name, wikidataId);
    UI.renderProfile(state.profile);
    state.pitch = null;               // a new client means any old pitch no longer applies
    UI.show("pitch-result", false);
  } catch (err) {
    UI.setCompanyError(err.message);
  } finally {
    UI.setResearching(false);
    UI.updateGenerateButton(!!state.profile);
  }
}

// ---------------------------------------------------------------- pitch
async function generatePitch() {
  const policies = UI.selectedPolicies();
  if (!state.profile) return UI.setPitchError("Research a company first.");
  if (!policies.length) return UI.setPitchError("Select at least one policy.");
  UI.setPitchError("");
  UI.setGenerating(true);
  try {
    state.pitch = await API.pitch(state.profile, policies);
    UI.renderPitch(state.pitch);
  } catch (err) {
    UI.setPitchError(err.message);
  } finally {
    UI.setGenerating(false);
    UI.updateGenerateButton(!!state.profile);
  }
}

async function downloadDeck(button) {
  if (!state.pitch) return;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Preparing…";
  try {
    const { blob, filename } = await API.exportDeck(state.pitch);
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    UI.setPitchError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

// ---------------------------------------------------------------- knowledge base
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

// ---------------------------------------------------------------- start-up
async function init() {
  const input = document.getElementById("company-input");
  input.addEventListener("input", onCompanyInput);
  input.addEventListener("keydown", onCompanyKeydown);
  input.addEventListener("blur", () => setTimeout(() => {
    if (document.activeElement !== input) closeSuggestions();  // only if focus really left the box
  }, 150));

  document.getElementById("suggestions").addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-index]");
    if (li) pickSuggestion(Number(li.dataset.index));
  });

  document.getElementById("company-form").addEventListener("submit", (e) => {
    e.preventDefault();
    researchCompany(input.value, state.pickedId);
  });

  // "Not the right company?" buttons
  document.getElementById("profile-result").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-wikidata-id]");
    if (!chip) return;
    input.value = chip.textContent.trim();
    state.pickedId = chip.dataset.wikidataId;
    researchCompany(input.value, state.pickedId);
  });

  document.getElementById("policy-picks").addEventListener("change", () => UI.updateGenerateButton(!!state.profile));
  document.getElementById("generate-btn").addEventListener("click", generatePitch);
  document.getElementById("pitch-result").addEventListener("click", (e) => {
    const button = e.target.closest("#download-btn");
    if (button) downloadDeck(button);
  });

  document.getElementById("policy-list").addEventListener("click", (e) => {
    const button = e.target.closest(".policy-row");
    if (button && !button.disabled) togglePolicy(button.closest("li"));
  });

  try {
    const [health, policies] = await Promise.all([API.health(), API.policies()]);
    UI.renderMode(health);
    UI.renderPolicies(policies);
    UI.renderPolicyPicks(policies);
    UI.updateGenerateButton(false);
    UI.renderStoreDate(health.fact_store_built_at);
  } catch (err) {
    UI.renderError(err.message);
  }
  input.focus();
}

document.addEventListener("DOMContentLoaded", init);