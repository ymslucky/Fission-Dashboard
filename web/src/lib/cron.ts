/**
 * Minimal 5-field cron parser (minute hour day-of-month month day-of-week).
 * Supports: * , - / and numbers. Enough for validation + next-fire preview;
 * the authoritative scheduler stays on the backend.
 */

export interface CronField {
  values: Set<number>;
  any: boolean;
}

function parseField(expr: string, min: number, max: number): CronField {
  const values = new Set<number>();
  for (const part of expr.split(',')) {
    const [rangePart, stepPart] = part.split('/');
    const step = stepPart !== undefined ? parseInt(stepPart, 10) : 1;
    if (!Number.isFinite(step) || step < 1) throw new Error('invalid step: ' + part);
    let lo: number;
    let hi: number;
    if (rangePart === '*') {
      lo = min;
      hi = max;
    } else if (rangePart.includes('-')) {
      const [a, b] = rangePart.split('-').map((x) => parseInt(x, 10));
      lo = a;
      hi = b;
    } else {
      lo = parseInt(rangePart, 10);
      hi = stepPart !== undefined ? max : lo;
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo < min || hi > max || lo > hi) {
      throw new Error('field out of range: ' + part);
    }
    for (let v = lo; v <= hi; v += step) values.add(v);
  }
  if (values.size === 0) throw new Error('empty field');
  return { values, any: expr.trim() === '*' };
}

export interface CronExpr {
  minute: CronField;
  hour: CronField;
  dom: CronField;
  month: CronField;
  dow: CronField;
  raw: string;
}

export function parseCron(expr: string): CronExpr {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) throw new Error('cron needs 5 fields');
  return {
    minute: parseField(parts[0], 0, 59),
    hour: parseField(parts[1], 0, 23),
    dom: parseField(parts[2], 1, 31),
    month: parseField(parts[3], 1, 12),
    dow: parseField(parts[4], 0, 6),
    raw: expr,
  };
}

export function isValidCron(expr: string): boolean {
  try {
    parseCron(expr);
    return true;
  } catch {
    return false;
  }
}

function matches(d: Date, c: CronExpr): boolean {
  const domOk = c.dom.any || c.dom.values.has(d.getDate());
  const dowOk = c.dow.any || c.dow.values.has(d.getDay());
  const dayOk = c.dom.any && c.dow.any ? true : c.dom.any ? dowOk : c.dow.any ? domOk : domOk || dowOk;
  return (
    dayOk &&
    c.month.values.has(d.getMonth() + 1) &&
    c.hour.values.has(d.getHours()) &&
    c.minute.values.has(d.getMinutes())
  );
}

export function nextFireTimes(expr: string, n: number, from: Date): Date[] {
  const c = parseCron(expr);
  const out: Date[] = [];
  const cur = new Date(from.getTime());
  cur.setSeconds(0, 0);
  cur.setMinutes(cur.getMinutes() + 1);
  const limit = new Date(cur.getTime() + 366 * 24 * 3600 * 1000);
  while (out.length < n && cur.getTime() <= limit.getTime()) {
    if (matches(cur, c)) out.push(new Date(cur.getTime()));
    cur.setMinutes(cur.getMinutes() + 1);
  }
  return out;
}

function pad(v: number): string {
  return String(v).padStart(2, '0');
}

export function formatDateTime(d: Date): string {
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}
