const STORAGE_KEY = "aigw.admin";
const WORKSPACE_KEY = "aigw.workspace";

export function token(): string {
  return sessionStorage.getItem(STORAGE_KEY) ?? "";
}

export function setToken(value: string): void {
  sessionStorage.setItem(STORAGE_KEY, value.trim());
}

export function clearToken(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

export function workspace(): string {
  return sessionStorage.getItem(WORKSPACE_KEY) ?? "";
}

export function setWorkspace(value: string): void {
  if (value) sessionStorage.setItem(WORKSPACE_KEY, value);
  else sessionStorage.removeItem(WORKSPACE_KEY);
  window.dispatchEvent(new Event("aigw-workspace"));
}

export class ApiError extends Error {
  constructor(message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const raw = token().trim();
  if (raw) {
    headers.set("authorization", raw.toLowerCase().startsWith("bearer ") ? raw : `Bearer ${raw}`);
  }
  const activeWorkspace = workspace();
  if (activeWorkspace) {
    headers.set("x-workspace", activeWorkspace);
  }
  if (init?.body) {
    headers.set("content-type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  const body = (await response.json().catch(() => ({}))) as { error?: { message?: string } };
  if (response.status === 401 && !path.startsWith("/api/bootstrap")) {
    clearToken();
    if (!window.location.pathname.startsWith("/setup")) {
      window.location.assign("/setup?reason=expired");
    }
    throw new ApiError(body.error?.message ?? "The gateway key was rejected.");
  }
  if (!response.ok) {
    throw new ApiError(body.error?.message ?? "Request failed.");
  }
  return body as T;
}
