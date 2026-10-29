import type { ReactElement } from 'react';

export type Kind = 'functions' | 'packages' | 'environments' | 'httptriggers' | 'timetriggers' | 'canary';

const KIND_COLORS: Record<Kind, string> = {
  functions: 'var(--kind-fn)',
  packages: 'var(--kind-pkg)',
  environments: 'var(--kind-env)',
  httptriggers: 'var(--kind-http)',
  timetriggers: 'var(--kind-timer)',
  canary: 'var(--kind-canary)',
};

/** Category colour chip — every resource kind carries its own hue. */
export default function KindDot({ kind }: { kind: Kind }): ReactElement {
  return <span className="kind-dot" style={{ background: KIND_COLORS[kind] }} aria-hidden="true" />;
}

export function kindColor(kind: Kind): string {
  return KIND_COLORS[kind];
}
