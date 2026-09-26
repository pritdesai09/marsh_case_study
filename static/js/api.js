// Every call to the backend goes through this file.
const API = {
  async send(path, options = {}) {
    try {
      return await fetch(path, options);
    } catch {
      throw new Error("Cannot reach the server. Is it running?");
    }
  },

  async request(path, options = {}) {
    const isForm = options.body instanceof FormData;
    const res = await API.send(path, {
      ...options,
      headers: isForm ? {} : { "Content-Type": "application/json" },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      // FastAPI sends {detail: "..."} for our errors and {detail: [...]} for validation errors
      const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
      throw new Error(detail || `Request failed (${res.status})`);
    }
    return data;
  },

  post: (path, body) => API.request(path, { method: "POST", body: JSON.stringify(body) }),

  // status & policy library
  health: () => API.request("/api/health"),
  policies: () => API.request("/api/policies"),
  facts: (policy) => API.request(`/api/facts?policy=${encodeURIComponent(policy)}`),
  uploadPolicy: (file, name, insurer) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    form.append("insurer", insurer);
    return API.request("/api/policies/upload", { method: "POST", body: form });
  },
  job: (jobId) => API.request(`/api/jobs/${encodeURIComponent(jobId)}`),
  removePolicy: (code) => API.request(`/api/policies/${encodeURIComponent(code)}`, { method: "DELETE" }),

  // generate: profile -> pitch -> audit
  searchCompanies: (q) => API.request(`/api/company-search?q=${encodeURIComponent(q)}`),
  profile: (company, wikidataId = null) => API.post("/api/profile", { company, wikidata_id: wikidataId }),
  pitch: (profile, policies) => API.post("/api/pitch", { profile, policies }),
  getPitch: (pitchId) => API.request(`/api/pitch/${encodeURIComponent(pitchId)}`),
  audit: (pitchId) => API.post("/api/audit", { pitch_id: pitchId }),
  getAudit: (auditId) => API.request(`/api/audit/${encodeURIComponent(auditId)}`),

  // advisor review
  claimAction: (auditId, claimId, action, text = null) =>
    API.post(`/api/audit/${encodeURIComponent(auditId)}/claims/${encodeURIComponent(claimId)}`, { action, text }),
  reportUrl: (auditId, fmt) => `/api/audit/${encodeURIComponent(auditId)}/report.${fmt}`,

  // Returns a Blob (the .pptx file) and the filename the server suggested
  async exportDeck(auditId) {
    const res = await API.send("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audit_id: auditId }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Download failed (${res.status})`);
    }
    const match = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
    return { blob: await res.blob(), filename: match ? match[1] : "Marsh_pitch.pptx" };
  },
};