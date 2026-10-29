/**
 * Minimal fetch wrapper for the console API.
 * All requests hit the same-origin /api prefix; the dev server proxies it.
 * Errors are normalised to Error objects carrying a `message`.
 */

export const BASE_URL = '/api';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
  if (!query) return url;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined) qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `${url}?${s}` : url;
}

async function parseError(res: Response): Promise<never> {
  let message = `Request failed (${res.status} ${res.statusText})`;
  try {
    const data = (await res.json()) as { message?: string; error?: string; detail?: { message?: string } | string };
    const detailMsg =
      typeof data.detail === 'object' && data.detail?.message ? data.detail.message
      : typeof data.detail === 'string' ? data.detail
      : undefined;
    message = detailMsg ?? data.message ?? data.error ?? message;
  } catch {
    // keep default message
  }
  throw new ApiError(message, res.status);
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, headers, ...rest } = options;
  const res = await fetch(buildUrl(path, query), {
    ...rest,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) return parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query']) => request<T>(path, { method: 'GET', query }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export function sseUrl(path: string, query?: RequestOptions['query']): string {
  return buildUrl(path, query);
}
/** 列表响应归一化：后端包装 {items:[...]} / {tools:[...]} 等，统一取数组。 */
export function asList<T>(data: unknown, key = 'items'): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === 'object') {
    const v = (data as Record<string, unknown>)[key];
    if (Array.isArray(v)) return v as T[];
  }
  return [];
}
