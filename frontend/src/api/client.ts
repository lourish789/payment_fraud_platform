// Single HTTP entry point for the console. Every request goes through `request`, which attaches the
// API key, parses the backend's error envelope into ApiError, and reports 401s so the session can end.

export interface ErrorEnvelope {
  error: { code: string; message: string; request_id?: string | null; details?: unknown };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string | null,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type QueryValue = string | number | boolean | null | undefined;
type Query = object; // a flat object of QueryValue fields; undefined/null/"" are dropped

let apiKey: string | null = null;
let onUnauthorized: () => void = () => {};

export function setApiKey(key: string | null) {
  apiKey = key;
}

export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries((query ?? {}) as Record<string, QueryValue>)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function parseError(res: Response): Promise<ApiError> {
  let body: Partial<ErrorEnvelope> | null = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error (proxy, gateway) */
  }
  const e = body?.error;
  return new ApiError(
    res.status,
    e?.code ?? `http_${res.status}`,
    e?.message ?? res.statusText ?? "request failed",
    e?.request_id ?? res.headers.get("x-request-id"),
    e?.details,
  );
}

export async function request<T>(
  method: "GET" | "POST",
  path: string,
  opts: { query?: Query; body?: unknown; form?: FormData; key?: string } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const key = opts.key ?? apiKey;
  if (key) headers.Authorization = `Bearer ${key}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(buildUrl(path, opts.query), { method, headers, body });
  if (!res.ok) {
    const err = await parseError(res);
    if (res.status === 401 && !opts.key) onUnauthorized();
    throw err;
  }
  return (await res.json()) as T;
}

export const get = <T>(path: string, query?: Query) => request<T>("GET", path, { query });
export const post = <T>(path: string, body?: unknown) => request<T>("POST", path, { body });
