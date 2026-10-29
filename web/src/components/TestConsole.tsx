import { useState, type ReactElement } from 'react';
import { useFunctionTest } from '../api/hooks';
import { StatusBadge } from './index';
import { useToast } from './Toast';

export interface TestOutcome {
  status?: number;
  latencyMs?: number;
  requestId?: string;
  component?: string;
  reason?: string;
  body?: string;
}

export interface TestConsoleProps {
  namespace: string;
  name: string;
  /** jump to the log tab pre-filtered by requestId */
  onJumpLogs?: (requestId: string) => void;
}

const METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'];

/** 调用测试 — POST /api/functions/{ns}/{name}/test with structured attribution. */
export default function TestConsole({ namespace, name, onJumpLogs }: TestConsoleProps): ReactElement {
  const [method, setMethod] = useState('GET');
  const [path, setPath] = useState('');
  const [headersRaw, setHeadersRaw] = useState('');
  const [body, setBody] = useState('');
  const [outcome, setOutcome] = useState<TestOutcome | null>(null);
  const test = useFunctionTest(namespace, name);
  const toast = useToast();

  const parseHeaders = (): Record<string, string> | undefined => {
    const trimmed = headersRaw.trim();
    if (!trimmed) return undefined;
    const headers: Record<string, string> = {};
    for (const line of trimmed.split('\n')) {
      const idx = line.indexOf(':');
      if (idx > 0) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    return headers;
  };

  const run = (): void => {
    setOutcome(null);
    test.mutate(
      { method, path, headers: parseHeaders(), body: body || undefined },
      {
        onSuccess: (res) => {
          setOutcome({
            status: res.status,
            latencyMs: res.latencyMs,
            requestId: res.requestId,
            component: res.component,
            reason: res.reason,
            body: res.body,
          });
        },
        onError: (err) => toast.showError('调用测试', err),
      },
    );
  };

  return (
    <div>
      <div className="form-row" style={{ gridTemplateColumns: '90px 1fr' }}>
        <div className="field">
          <span>方法</span>
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="field">
          <span>路径（相对函数 URL）</span>
          <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/" />
        </div>
      </div>
      <div className="field">
        <span>Headers（每行 Key: Value）</span>
        <textarea rows={3} value={headersRaw} onChange={(e) => setHeadersRaw(e.target.value)} className="mono" style={{ fontSize: 12 }} />
      </div>
      <div className="field">
        <span>Body</span>
        <textarea rows={4} value={body} onChange={(e) => setBody(e.target.value)} className="mono" style={{ fontSize: 12 }} />
      </div>
      <button type="button" className="btn pri" disabled={test.isPending} onClick={run}>
        {test.isPending ? '调用中…' : '发起调用'}
      </button>

      {outcome ? (
        <div className="obs-card" style={{ marginTop: 14 }}>
          <h3>归因卡片</h3>
          <table className="plain">
            <tbody>
              <tr>
                <th>状态</th>
                <td><StatusBadge status={outcome.status != null ? String(outcome.status) : 'UNKNOWN'} /></td>
              </tr>
              <tr><th>latencyMs</th><td className="num">{outcome.latencyMs !== undefined ? outcome.latencyMs + ' ms' : '—'}</td></tr>
              <tr><th>requestId</th><td className="mono">{outcome.requestId ?? '—'}</td></tr>
              <tr><th>component</th><td className="mono">{outcome.component ?? '—'}</td></tr>
              <tr><th>reason</th><td>{outcome.reason ?? '—'}</td></tr>
              <tr>
                <th>body 预览</th>
                <td><pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>{outcome.body ?? '—'}</pre></td>
              </tr>
            </tbody>
          </table>
          {outcome.requestId && onJumpLogs ? (
            <button type="button" className="btn sm" style={{ marginTop: 8 }} onClick={() => onJumpLogs(outcome.requestId!)}>
              用 requestId 跳日志
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
