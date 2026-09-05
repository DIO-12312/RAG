export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ErrorPayload {
  code?: string;
  message?: string;
}

function apiUrl(path: string): string {
  return new URL(path, window.location.origin).toString();
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    credentials: "include",
    ...init,
  });

  if (!response.ok) {
    const payload = (await response.json()) as ErrorPayload;
    throw new ApiError(response.status, payload.code ?? "REQUEST_FAILED", payload.message ?? "请求失败。");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
