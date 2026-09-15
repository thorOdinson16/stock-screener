const BASE = "/api";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  const text = await res.text();
  if (!res.ok) {
    throw new Error(text || `HTTP ${res.status}`);
  }
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    let message = text || `HTTP ${res.status}`;
    try {
      const parsed = JSON.parse(text);
      const detail = parsed?.detail;
      message = typeof detail === "string" ? detail : detail?.message ?? message;
    } catch {
      /* keep raw text */
    }
    const error = new Error(message);
    (error as Error & { status?: number }).status = res.status;
    throw error;
  }
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export function buildQuery(params: Record<string, unknown>): string {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      value.forEach((v) => usp.append(key, String(v)));
    } else {
      usp.append(key, String(value));
    }
  }
  const q = usp.toString();
  return q ? `?${q}` : "";
}
