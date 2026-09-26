// Page state and flow: one form -> profile -> pitch -> audit -> advisor review -> download.
const state = {
  policies: [],
  checked: new Set(),   // policy codes ticked in the form
  pitch: null,
  report: null,         // audit report + review_summary from the server
  selected: null,       // claim id open in the inspector
  editing: false,
  draft: null,          // edited wording while it is being re-audited
  busyClaim: false,
  filter: "all",        // audit tab filter
  suggestions: [],
  activeSuggestion: -1,
  pickedId: null,       // Wikidata id chosen from the suggestions, if any
  searchTimer: null,
  searchSeq: 0,
  polling: new Set(),   // upload jobs being watched
};
const factsCache = {};

// ---------------------------------------------------------------- company search box
function closeSuggestions() {
  clearTimeout(state.searchTimer);
  state.searchSeq++;
  state.suggestions = [];
  state.activeSuggestion = -1;
  UI.renderSuggestions([]);
}

function onCompanyInput(e) {
  state.pickedId = null;
  UI.setError("form-error", "");
  clearTimeout(state.searchTimer);
  const q = e.target.value.trim();
  if (q.length < 2) return closeSuggestions();
  state.searchTimer = setTimeout(async () => {
    const seq = ++state.searchSeq;
    try {
      const items = await API.searchCompanies(q);
      const input = document.getElementById("company-input");
      if (seq !== state.searchSeq || document.activeElement !== input) return;
      state.suggestions = items;
      state.activeSuggestion = -1;
      UI.renderSuggestions(items);
    } catch {
      closeSuggestions();
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

// ---------------------------------------------------------------- policies
async function loadPolicies() {
  state.policies = await API.policies();
  UI.renderPolicyPicks(state.policies, state.checked);
  state.policies.filter(p => p.status === "processing" && p.job_id).forEach(p => watchJob(p.job_id, p.code));
}

function onPicksChange() {
  state.checked = new Set(UI.selectedPolicies());
  UI.enforcePolicyLimit();
  UI.setError("form-error", "");
}

// ---------------------------------------------------------------- upload a brochure
function toggleUpload(open) {
  UI.show("upload-panel", open);
  UI.show("upload-toggle", !open);
  UI.setError("upload-error", "");
}

async function uploadPolicy() {
  const file = document.getElementById("upload-file").files[0];
  const name = document.getElementById("upload-name").value.trim();
  const insurer = document.getElementById("upload-insurer").value.trim();
  if (!file) return UI.setError("upload-error", "Choose a PDF file.");
  if (!/\.pdf$/i.test(file.name)) return UI.setError("upload-error", "The file must be a PDF.");
  if (file.size > 15 * 1024 * 1024) return UI.setError("upload-error", "The PDF is larger than 15 MB.");
  if (!name || !insurer) return UI.setError("upload-error", "Enter the policy name and the insurer.");

  const button = document.getElementById("upload-btn");
  button.disabled = true;
  button.textContent = "Uploading…";
  try {
    const job = await API.uploadPolicy(file, name, insurer);
    ["upload-file", "upload-name", "upload-insurer"].forEach(id => { document.getElementById(id).value = ""; });
    toggleUpload(false);
    await loadPolicies();
    watchJob(job.job_id, job.code);
  } catch (err) {
    UI.setError("upload-error", err.message);
  } finally {
    button.disabled = false;
    button.textContent = "Upload and read";
  }
}

function watchJob(jobId, code) {
  if (state.polling.has(jobId)) return;
  state.polling.add(jobId);
  const tick = async () => {
    let job;
    try {
      job = await API.job(jobId);
    } catch {
      state.polling.delete(jobId);
      return;
    }
    if (job.state === "running") return setTimeout(tick, 3000);
    state.polling.delete(jobId);
    if (job.state === "done" && state.checked.size < UI.MAX_POLICIES) state.checked.add(code);
    await loadPolicies();
  };
  setTimeout(tick, 3000);
}

// ---------------------------------------------------------------- generate
async function generate(wikidataId = state.pickedId) {
  const input = document.getElementById("company-input");
  const company = input.value.trim();
  const policies = UI.selectedPolicies();
  closeSuggestions();
  if (!company) {
    UI.setError("form-error", "Enter the client's company name.");
    return input.focus();
  }
  if (!policies.length) return UI.setError("form-error", "Choose at least one policy document.");

  UI.setError("form-error", "");
  UI.setBusy(true);
  UI.show("result", false);
  const steps = { profile: "active", pitch: "", audit: "" };
  let current = "profile";
  UI.setProgress(steps);
  try {
    const profile = await API.profile(company, wikidataId);
    steps.profile = "done"; steps.pitch = "active"; current = "pitch";
    UI.setProgress(steps);
    const pitch = await API.pitch(profile, policies);
    steps.pitch = "done"; steps.audit = "active"; current = "audit";
    UI.setProgress(steps);
    const report = await API.audit(pitch.id);
    steps.audit = "done";
    UI.setProgress(steps);
    showResult(pitch, report);
    history.replaceState(null, "", `#audit=${report.audit_id}`);
  } catch (err) {
    steps[current] = "error";
    UI.setProgress(steps);
    UI.setError("form-error", err.message);
  } finally {
    UI.setBusy(false);
  }
}

// ---------------------------------------------------------------- result & review
function context() {
  const r = state.report;
  return {
    claims: Object.fromEntries(r.claims.map(c => [c.id, c])),
    states: r.review_summary.states,
    blockers: r.review_summary.blockers,
    selected: state.selected,
    recommended: state.pitch.recommended,
    total: state.pitch.slides.length,
  };
}

function renderReview() {
  const ctx = context();
  UI.renderHead(state.pitch, state.report);
  UI.renderSlides(state.pitch, ctx);
  UI.renderInspector(ctx.claims[state.selected], ctx, { editing: state.editing, busy: state.busyClaim, draft: state.draft });
  UI.renderAuditTab(state.report, state.filter);
}

function showResult(pitch, report) {
  state.pitch = pitch;
  state.report = report;
  state.selected = null;
  state.editing = false;
  state.filter = "all";
  UI.renderProfile(pitch.profile);
  UI.renderRanking(pitch);
  renderReview();
  UI.selectTab("slides");
  UI.show("result", true);
  document.getElementById("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function selectClaim(id, { scroll = false } = {}) {
  state.selected = id;
  state.editing = false;
  UI.selectTab("slides");
  renderReview();
  if (scroll) {
    document.querySelector(`#slides [data-claim-id="${CSS.escape(id)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function nextIssue() {
  const blockers = state.report.review_summary.blockers;
  if (!blockers.length) return;
  const i = blockers.indexOf(state.selected);
  selectClaim(blockers[(i + 1) % blockers.length], { scroll: true });
}

async function claimAction(action) {
  const id = state.selected;
  if (!id) return;
  let text = null;
  if (action === "save-edit") {
    text = document.getElementById("edit-text").value.trim();
    if (!text) return UI.setError("insp-error", "The statement can't be empty.");
    action = "edit";
    state.draft = text;
  }
  state.busyClaim = true;
  renderReview();
  try {
    state.report = await API.claimAction(state.report.audit_id, id, action, text);
    state.editing = false;
    state.draft = null;
  } catch (err) {
    state.busyClaim = false;
    renderReview();
    return UI.setError("insp-error", err.message);
  }
  state.busyClaim = false;
  renderReview();
}

function onInspectorClick(e) {
  const button = e.target.closest("[data-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (action === "next-issue") return nextIssue();
  if (action === "close-inspector") {
    state.selected = null;
    state.editing = false;
    return renderReview();
  }
  if (action === "edit") {
    state.editing = true;
    state.draft = null;
    renderReview();
    const box = document.getElementById("edit-text");
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
    return;
  }
  if (action === "cancel-edit") {
    state.editing = false;
    state.draft = null;
    return renderReview();
  }
  claimAction(action);
}

async function downloadDeck(button) {
  button.disabled = true;
  button.textContent = "Preparing…";
  try {
    const { blob, filename } = await API.exportDeck(state.report.audit_id);
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    state.report = await API.getAudit(state.report.audit_id);  // records the sign-off time
    renderReview();
  } catch (err) {
    alert(err.message);
    renderReview();
  }
}

// Reopen a pitch from the link (#audit=...), e.g. after a page refresh
async function restoreFromLink() {
  const match = /audit=([0-9a-f]{10})/.exec(location.hash);
  if (!match) return;
  try {
    const report = await API.getAudit(match[1]);
    const pitch = await API.getPitch(report.pitch_id);
    document.getElementById("company-input").value = pitch.company;
    showResult(pitch, report);
  } catch {
    history.replaceState(null, "", location.pathname);
  }
}

// ---------------------------------------------------------------- policy library
async function openLibrary() {
  const dialog = document.getElementById("library");
  dialog.showModal();
  try {
    const [health, policies] = await Promise.all([API.health(), API.policies()]);
    UI.renderStoreDate(health.fact_store_built_at);
    UI.renderPolicies(policies);
  } catch (err) {
    document.getElementById("policy-list").innerHTML = `<li><p class="empty">${UI.escape(err.message)}</p></li>`;
  }
}

async function togglePolicyFacts(li) {
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

async function removePolicy(code) {
  if (!confirm("Remove this uploaded policy and all its facts?")) return;
  try {
    await API.removePolicy(code);
    delete factsCache[code];
    state.checked.delete(code);
    UI.renderPolicies(await API.policies());
    await loadPolicies();
  } catch (err) {
    alert(err.message);
  }
}

// ---------------------------------------------------------------- start-up
async function init() {
  const input = document.getElementById("company-input");
  input.addEventListener("input", onCompanyInput);
  input.addEventListener("keydown", onCompanyKeydown);
  input.addEventListener("blur", () => setTimeout(() => {
    if (document.activeElement !== input) closeSuggestions();
  }, 150));
  document.getElementById("suggestions").addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-index]");
    if (li) pickSuggestion(Number(li.dataset.index));
  });

  document.getElementById("pitch-form").addEventListener("submit", (e) => {
    e.preventDefault();
    generate();
  });
  document.getElementById("policy-picks").addEventListener("change", onPicksChange);
  document.getElementById("upload-toggle").addEventListener("click", () => toggleUpload(true));
  document.getElementById("upload-cancel").addEventListener("click", () => toggleUpload(false));
  document.getElementById("upload-btn").addEventListener("click", uploadPolicy);

  // Result area
  document.getElementById("result-head").addEventListener("click", (e) => {
    if (e.target.closest("#download-btn:not([disabled])")) downloadDeck(e.target.closest("#download-btn"));
    if (e.target.closest("[data-action=next-issue]")) nextIssue();
  });
  document.querySelector(".tabs").addEventListener("click", (e) => {
    const tab = e.target.closest("[data-tab]");
    if (tab) UI.selectTab(tab.dataset.tab);
  });
  document.getElementById("slides").addEventListener("click", (e) => {
    const claim = e.target.closest("[data-claim-id]");
    if (claim) selectClaim(claim.dataset.claimId);
  });
  document.getElementById("inspector").addEventListener("click", onInspectorClick);
  const auditTab = document.getElementById("tab-audit");
  auditTab.addEventListener("click", (e) => {
    const filter = e.target.closest("[data-filter]");
    if (filter) {
      state.filter = filter.dataset.filter;
      return UI.renderAuditTab(state.report, state.filter);
    }
    const row = e.target.closest("tr[data-claim-id]");
    if (row) selectClaim(row.dataset.claimId, { scroll: true });
  });
  auditTab.addEventListener("keydown", (e) => {
    const row = e.target.closest("tr[data-claim-id]");
    if (row && e.key === "Enter") selectClaim(row.dataset.claimId, { scroll: true });
  });
  document.getElementById("tab-profile").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-wikidata-id]");
    if (!chip) return;
    input.value = chip.dataset.label;
    state.pickedId = chip.dataset.wikidataId;
    window.scrollTo({ top: 0, behavior: "smooth" });
    generate(state.pickedId);
  });

  // Policy library
  document.getElementById("library-btn").addEventListener("click", openLibrary);
  document.getElementById("library-close").addEventListener("click", () => document.getElementById("library").close());
  document.getElementById("policy-list").addEventListener("click", (e) => {
    const remove = e.target.closest("[data-remove]");
    if (remove) return removePolicy(remove.dataset.remove);
    const row = e.target.closest(".policy-row");
    if (row && !row.disabled) togglePolicyFacts(row.closest("li"));
  });

  try {
    const health = await API.health();
    UI.renderMode(health);
    state.policies = await API.policies();
    state.policies.filter(p => p.status === "ready" && p.verified > 0).slice(0, UI.MAX_POLICIES)
      .forEach(p => state.checked.add(p.code));
    await loadPolicies();
  } catch (err) {
    UI.renderOffline(err.message);
  }
  await restoreFromLink();
  if (!state.pitch) input.focus();
}

document.addEventListener("DOMContentLoaded", init);