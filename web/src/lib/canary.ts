/**
 * Canary submit-body contract helpers (shared by the create wizard and selfcheck).
 * Field names mirror the authoritative backend list in server/routers/canary.py
 * (CANARY_REQUIRED): newfunction/oldfunction/trigger/weightincrement/
 * duration/failurethreshold/failureType.
 */
export const CANARY_FIELDS = [
  'newfunction',
  'oldfunction',
  'trigger',
  'weightincrement',
  'duration',
  'failurethreshold',
  'failureType',
] as const;

export type CanaryField = (typeof CANARY_FIELDS)[number];

export const CANARY_FAILURE_TYPES = ['failure', 'timeout'] as const;

/** True when every value is an integer in 0..100 and the values sum to exactly 100. */
export function weightsSumTo100(weights: Record<string, number>): boolean {
  const entries = Object.entries(weights);
  if (entries.length === 0) return false;
  let total = 0;
  for (const [, v] of entries) {
    if (typeof v !== 'number' || !Number.isInteger(v) || v < 0 || v > 100) return false;
    total += v;
  }
  return total === 100;
}

/** Mirror of the wizard validate(): returns the first violated field or null. */
export function validateCanaryBody(body: Record<string, unknown>): CanaryField | null {
  for (const k of CANARY_FIELDS) {
    const v = body[k];
    if (v === undefined || v === null || v === '') return k;
  }
  if (!(typeof body.weightincrement === 'number' && body.weightincrement > 0 && body.weightincrement <= 100)) return 'weightincrement';
  if (typeof body.duration !== 'string' || !/^[0-9]+(ms|s|m|h|d)$/.test(body.duration)) return 'duration';
  if (!(typeof body.failurethreshold === 'number' && body.failurethreshold > 0)) return 'failurethreshold';
  if (!(CANARY_FAILURE_TYPES as readonly string[]).includes(String(body.failureType))) return 'failureType';
  return null;
}
