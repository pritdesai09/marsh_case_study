// Every call to the backend goes through this file.
const API = {
  async request(path, options = {}) {
    let res;
    try {
      res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
    } catch {
      throw new Error("Cannot reach the server. Is it running?");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      // FastAPI sends {detail: "..."} for our errors and {detail: [...]} for validation errors
      const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
      throw new Error(detail || `Request failed (${res.status})`);
    }
    return data;
  },

  health: () => API.request("/api/health"),
  policies: () => API.request("/api/policies"),
  facts: (policy) => API.request(`/api/facts?policy=${encodeURIComponent(policy)}`),
  searchCompanies: (q) => API.request(`/api/company-search?q=${encodeURIComponent(q)}`),
  profile: (company, wikidataId = null) =>
    API.request("/api/profile", {
      method: "POST",
      body: JSON.stringify({ company, wikidata_id: wikidataId }),
    }),
};