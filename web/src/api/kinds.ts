import type { ResourceKind } from './types';

export const KINDS = ['functions', 'packages', 'environments', 'httptriggers', 'timetriggers'] as const;

export function isResourceKind(v: string): v is ResourceKind {
  return (KINDS as readonly string[]).includes(v);
}

export function kindPath(kind: ResourceKind, namespace?: string, name?: string): string {
  let p = `/resources/${kind}`;
  if (namespace) p += `/${encodeURIComponent(namespace)}`;
  if (name) p += `/${encodeURIComponent(name)}`;
  return p;
}
