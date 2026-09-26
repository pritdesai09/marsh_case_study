async function init() {
  try {
    const [health, policies] = await Promise.all([API.health(), API.policies()]);
    UI.renderMode(health);
    UI.renderPolicies(policies);
  } catch (err) {
    UI.renderError(err.message);
  }
}

document.addEventListener("DOMContentLoaded", init);