import { useMemo, type ReactElement } from 'react';
import { useNavigate } from 'react-router-dom';
import { useList, useMetrics, useOverview, type FunctionMetric } from '../api/hooks';
import { StatusBadge } from '../components';
import type { Function } from '../api/types';

interface ComponentRow {
  name: string;
  ready: number;
  desired: number;
  status: string;
}

function extractComponents(ov: Record<string, unknown> | undefined): ComponentRow[] {
  if (!ov) return [];
  const raw = (ov.components ?? ov.core ?? ov.coreComponents ?? ov.pods) as unknown;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((c): c is Record<string, unknown> => typeof c === 'object' && c !== null)
    .map((c) => ({
      name: String(c.name ?? c.app ?? c.component ?? '?'),
      ready: Number(c.ready ?? c.readyReplicas ?? 0),
      desired: Number(c.desired ?? c.replicas ?? c.pods ?? 0),
      status: String(c.status ?? (Number(c.ready ?? 0) >= Number(c.desired ?? 1) ? 'READY' : 'PENDING')),
    }));
}

/** 函数量级点阵：按 CPU 用量排点径，异常置顶插旗。 */
function FunctionDots({ functions, metrics }: { functions: Function[]; metrics: FunctionMetric[] | null }): ReactElement {
  const nav = useNavigate();
  const rows = useMemo(() => {
    const cpuOf = (f: Function): number => metrics?.find((m) => m.name === f.metadata.name)?.cpuMilli ?? 0;
    const sorted = [...functions].sort((a, b) => cpuOf(b) - cpuOf(a));
    const max = Math.max(1, ...sorted.map(cpuOf));
    return { sorted, max, cpuOf };
  }, [functions, metrics]);

  if (rows.sorted.length === 0) return <div className="empty">尚无函数观测点</div>;

  const W = 340;
  const rowH = 22;
  const height = rows.sorted.length * rowH + 8;

  return (
    <svg width="100%" height={height} viewBox={'0 0 ' + W + ' ' + height} role="img" aria-label="函数量级点阵">
      {rows.sorted.map((f, i) => {
        const cpu = rows.cpuOf(f);
        const r = metrics ? 3 + 6 * Math.sqrt(cpu / rows.max) : 3;
        const y = i * rowH + 14;
        const errored = f.status?.ready === false;
        return (
          <g key={f.metadata.uid ?? f.metadata.name} style={{ cursor: 'pointer' }} onClick={() => nav('/functions/' + encodeURIComponent(f.metadata.namespace) + '/' + encodeURIComponent(f.metadata.name))}>
            {errored ? <rect x="0" y={y - 9} width={W} height={rowH - 4} fill="var(--alarm-soft)" /> : null}
            <circle cx="12" cy={y - 3} r={r} fill={errored ? 'var(--alarm)' : 'var(--obs)'} opacity={errored ? 1 : 0.85} />
            <text x="26" y={y} fontFamily="Noto Sans SC" fontSize="11" fill={errored ? 'var(--alarm)' : 'var(--ink)'}>
              {f.metadata.name}
            </text>
            <text x={W - 6} y={y} textAnchor="end" fontFamily="IBM Plex Mono" fontSize="10" fill="var(--gray-teal)">
              {metrics ? (cpu > 0 ? cpu + 'm' : '—') : '—'}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export default function Overview(): ReactElement {
  const overview = useOverview();
  const metrics = useMetrics();
  const functions = useList<Function>('functions');

  const counts = overview.data?.counts;
  const components = extractComponents(overview.data as Record<string, unknown> | undefined);

  const errorFns = (functions.data ?? []).filter((f) => f.status?.ready === false);

  return (
    <main className="page">
      {/* 五行计数观测带 — 一条横向观测带，不做等大卡片 */}
      <div className="count-band" aria-label="资源计数观测带">
        {[
          { label: '函数 FUNCTIONS', kind: 'var(--kind-fn)', n: counts?.functions },
          { label: '包 PACKAGES', kind: 'var(--kind-pkg)', n: counts?.packages },
          { label: '环境 ENVIRONMENTS', kind: 'var(--kind-env)', n: counts?.environments },
          { label: 'HTTP 触发器', kind: 'var(--kind-http)', n: counts?.httpTriggers ?? counts?.httptriggers },
          { label: '定时触发器', kind: 'var(--kind-timer)', n: counts?.timeTriggers ?? counts?.timetriggers },
        ].map((c) => (
          <div className="count-cell" key={c.label}>
            <div className="n" style={{ color: typeof c.n === 'number' && c.n > 0 ? 'var(--ink)' : 'var(--gray-teal)' }}>
              {/* BE 查询失败时计数为 -1，按无数据显示 */}
              {typeof c.n === 'number' && c.n >= 0 ? String(c.n).padStart(2, '0') : '—'}
            </div>
            <div className="l">
              <span className="kind-dot" style={{ background: c.kind }} />
              {c.label}
            </div>
          </div>
        ))}
      </div>

      {overview.isError ? (
        <div className="banner" style={{ marginLeft: 20, marginRight: 20 }}>
          ▲ fission API 概览不可达：{overview.error instanceof Error ? overview.error.message : '未知错误'}
        </div>
      ) : null}

      <div style={{ display: 'flex', gap: 0, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <section style={{ flex: '1 1 380px', minWidth: 0 }}>
          <div className="pane-h">核心组件就绪表<span>fission core components</span></div>
          <div className="page-pad" style={{ paddingTop: 0 }}>
            {components.length === 0 ? (
              <div className="empty">概览数据未含组件明细 — 后端扩展中</div>
            ) : (
              <table className="plain">
                <thead>
                  <tr><th>组件</th><th>PODS</th><th>READY</th><th>状态</th></tr>
                </thead>
                <tbody>
                  {components.map((c) => {
                    const ok = c.ready === c.desired;
                    return (
                      <tr key={c.name}>
                        <td className="fn-name">{c.name}</td>
                        <td className="num">{c.desired}</td>
                        <td className="num">{c.ready}/{c.desired}</td>
                        <td><StatusBadge status={ok ? 'READY' : 'DEGRADED'} tone={ok ? 'ok' : 'pending'} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </section>

        <section style={{ flex: '1 1 360px', minWidth: 0, borderLeft: '1px solid var(--hairline)' }}>
          <div className="pane-h">
            函数量级点阵
            <span>{functions.data?.length ?? 0} · 按 CPU 排点径{errorFns.length ? ' · ' + errorFns.length + ' 异常置顶' : ''}{metrics.unavailable ? '（无指标数据）' : ''}</span>
          </div>
          <div className="page-pad" style={{ paddingTop: 0 }}>
            <FunctionDots functions={functions.data ?? []} metrics={metrics.functions} />
          </div>
        </section>
      </div>

      {metrics.unavailable || (metrics.functions ?? []).length === 0 ? (
        <div className="banner" style={{ marginBottom: 20 }}>
          ◇ 运行指标来自 <span className="mono">metrics.k8s.io</span>，调用率曲线需接入 Prometheus。
        </div>
      ) : (
        <div className="banner blue" style={{ marginBottom: 20 }}>
          ◇ Prometheus 已接入 — 调用率与延迟曲线将在函数检查器中呈现。
        </div>
      )}
    </main>
  );
}
