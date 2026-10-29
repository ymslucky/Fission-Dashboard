/**
 * Unified error-message extraction.
 * Backend error bodies carry the human message inside response.detail.message;
 * older paths return { message } or plain text. Never let an error be silent.
 */
export interface ErrorBody {
  message?: string;
  error?: string;
  detail?: { message?: string } | string;
}

export function getErrMessage(res: unknown): string {
  if (res instanceof Error) return res.message || res.name;
  if (typeof res === 'string') return res;
  if (typeof res === 'object' && res !== null) {
    const b = res as ErrorBody;
    if (typeof b.detail === 'object' && b.detail?.message) return b.detail.message;
    if (typeof b.detail === 'string') return b.detail;
    if (b.message) return b.message;
    if (b.error) return b.error;
    try {
      return JSON.stringify(res);
    } catch {
      return String(res);
    }
  }
  return String(res);
}

/** True for 501 (feature not configured) / 403 (RBAC denied) ApiErrors. */
export function errStatus(err: unknown): number | null {
  if (typeof err === 'object' && err !== null && 'status' in err) {
    const s = (err as { status?: unknown }).status;
    if (typeof s === 'number') return s;
  }
  return null;
}
