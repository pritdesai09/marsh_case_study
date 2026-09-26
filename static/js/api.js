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
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    return data;
  },

  health: () => API.request("/api/health"),
  policies: () => API.request("/api/policies"),
};