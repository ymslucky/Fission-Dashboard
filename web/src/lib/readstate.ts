/**
 * Unread change tracking for list rows (异常/变更驻留).
 * A row keeps its pale vermilion ground until the user has viewed it in this
 * session. State lives in sessionStorage — deliberately ephemeral, like the
 * shift log at an observation post.
 */

const KEY = 'fission-console.viewed';

function load(): Set<string> {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return new Set<string>();
    const arr = JSON.parse(raw) as string[];
    return new Set<string>(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set<string>();
  }
}

function save(s: Set<string>): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(Array.from(s)));
  } catch {
    // storage unavailable — degradation is acceptable
  }
}

export function isViewed(id: string): boolean {
  return load().has(id);
}

export function markViewed(id: string): void {
  const s = load();
  if (!s.has(id)) {
    s.add(id);
    save(s);
  }
}

export function rowId(kind: string, ns: string, name: string): string {
  return kind + '/' + ns + '/' + name;
}
