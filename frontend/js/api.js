export class ApiError extends Error {
  constructor(status, detail, { network = false } = {}) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.network = network;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch("/api" + path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "", { network: true });
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") detail = data.detail;
      // FastAPI validation errors: a list of {loc, msg}; name the fields instead of dumping JSON.
      else if (Array.isArray(data.detail)) detail = `Проверьте поле: ${[...new Set(data.detail.map((e) => (e.loc || []).slice(1).join(".") || "запрос"))].join(", ")} — пустое или слишком длинное`;
      else detail = JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json();
}

export const get = (path) => api(path);
export const post = (path, body = {}) => api(path, { method: "POST", body });
export const put = (path, body = {}) => api(path, { method: "PUT", body });
export const del = (path) => api(path, { method: "DELETE" });

export function query(params) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "" && value !== false) search.set(key, value);
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}
