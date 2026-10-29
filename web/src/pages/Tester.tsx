import { useState, type ReactElement } from 'react';
import { useList } from '../api/hooks';
import type { Function } from '../api/types';
import { TestConsole } from '../components';

/** 调用测试台 — 与函数检查器共用 TestConsole。 */
export default function Tester(): ReactElement {
  const functions = useList<Function>('functions');
  const fns = functions.data ?? [];
  const [sel, setSel] = useState<{ ns: string; name: string } | null>(null);
  const current = sel ?? (fns[0] ? { ns: fns[0].metadata.namespace, name: fns[0].metadata.name } : null);

  return (
    <main className="page page-pad">
      <div className="pane-h">调用测试台<span>结构化归因：status / latencyMs / requestId / component / reason</span></div>
      {current ? (
        <div style={{ maxWidth: 640 }}>
          <div className="field">
            <span>函数</span>
            <select
              value={current.ns + '/' + current.name}
              onChange={(e) => {
                const [ns, name] = e.target.value.split('/');
                setSel({ ns, name });
              }}
            >
              {fns.map((f) => (
                <option key={f.metadata.uid ?? f.metadata.name} value={f.metadata.namespace + '/' + f.metadata.name}>
                  {f.metadata.namespace}/{f.metadata.name}
                </option>
              ))}
            </select>
          </div>
          <TestConsole namespace={current.ns} name={current.name} />
        </div>
      ) : functions.isLoading ? (
        <div className="empty">加载中…</div>
      ) : (
        <div className="empty">暂无函数可测试 — 请先在函数中心创建。</div>
      )}
    </main>
  );
}
