import { useEffect, useRef, type ReactElement } from 'react';

export interface LogPaneProps {
  lines: string[];
  autoscroll?: boolean;
  height?: string;
  connected?: boolean;
}

/** Monospace log stream surface, follows the tail while streaming. */
export default function LogPane({ lines, autoscroll = true, height = '320px', connected }: LogPaneProps): ReactElement {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoscroll && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [lines, autoscroll]);

  return (
    <div>
      {connected !== undefined ? (
        <div className="cap" style={{ marginBottom: 4 }}>
          {connected ? '● live — follow 中' : '○ 静止'}
        </div>
      ) : null}
      <div
        ref={ref}
        style={{
          height,
          overflow: 'auto',
          background: 'var(--ink)',
          color: '#c9d4d8',
          fontFamily: 'var(--font-mono)',
          fontVariantNumeric: 'tabular-nums',
          fontSize: 12,
          lineHeight: 1.6,
          padding: '8px 10px',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {lines.length === 0 ? <span style={{ color: 'var(--gray-teal)' }}>(无日志)</span> : lines.join('\n')}
      </div>
    </div>
  );
}
