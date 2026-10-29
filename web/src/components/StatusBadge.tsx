import type { ReactElement } from 'react';

export type StatusTone = 'ok' | 'pending' | 'error' | 'neutral';

export interface StatusBadgeProps {
  status: string;
  /** short qualifier, e.g. PENDING 特化中 */
  qualifier?: string;
  tone?: StatusTone;
}

export function toneForStatus(status: string): StatusTone {
  const s = status.toLowerCase();
  if (['ok', 'ready', 'succeeded', 'success', 'running', 'none', 'built', 'active', 'true'].includes(s)) return 'ok';
  if (['pending', 'build', 'building', 'waiting', 'containercreating', 'provisioning', 'progressing'].includes(s)) return 'pending';
  if (['failed', 'error', 'crashloopbackoff', 'imagepullbackoff', 'errimagepull', 'false', 'unavailable'].includes(s)) return 'error';
  return 'neutral';
}

/** Shape glyphs for redundant status coding: READY=● ERROR=■ PENDING=▲ */
function Glyph({ tone }: { tone: StatusTone }): ReactElement | null {
  if (tone === 'ok') return <span className="sig sig-dot" aria-hidden="true" />;
  if (tone === 'error') return <span className="sig sig-square" aria-hidden="true" />;
  if (tone === 'pending') return <span className="sig sig-tri" aria-hidden="true" />;
  return null;
}

/** Status badge with redundant coding: colour + shape + text. */
export default function StatusBadge({ status, qualifier, tone }: StatusBadgeProps): ReactElement {
  const t = tone ?? toneForStatus(status);
  const cls = t === 'ok' ? 'status-ok' : t === 'error' ? 'status-error' : t === 'pending' ? 'status-pending' : 'status-neutral';
  return (
    <span className={'status ' + cls}>
      <Glyph tone={t} />
      {status}
      {qualifier ? ' ' + qualifier : ''}
    </span>
  );
}
