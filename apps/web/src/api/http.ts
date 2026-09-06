export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) { super(message); this.name = "ApiError"; }
}
export function csrfHeaders(): Record<string,string> {
  const cookie = document.cookie.split("; ").find((item) => item.startsWith("rag_csrf="));
  return cookie ? { "X-CSRF-Token": decodeURIComponent(cookie.slice(9)) } : {};
}
export async function checkedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  for (const [key,value] of Object.entries(csrfHeaders())) headers.set(key,value);
  if (typeof init.body === "string") headers.set("Content-Type", "application/json");
  const response = await fetch(new URL(path, window.location.origin), { ...init, headers, credentials: "include" });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) window.dispatchEvent(new Event("auth-expired"));
    throw new ApiError(response.status, payload.code ?? "REQUEST_FAILED", payload.message ?? "请求失败，请重试。");
  }
  return response;
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await checkedFetch(path, init);
  return response.status === 204 ? undefined as T : await response.json() as T;
}
