import { type ReactElement } from 'react';
import { promErrorReason, promScalar, useMeshProbe, useProm } from '../api/extend';
import { StatusBadge } from '../components';
import { getErrMessage, errStatus } from '../lib/err';

const Q = {
  p50: 'histogram_quantile(0.50, sum(rate(fission_function_duration_seconds_bucket[5m])) by (le))',
  p95: 'histogram_quantile(0.95, sum(rate(fission_function_duration_seconds_bucket[5m])) by (le))',
  p99: 'histogram_quantile(0.99, sum(rate(fission_function_duration_seconds_bucket[5m])) by (le))',
  rps: 'sum(rate(fission_function_calls_total[5m]))',
  ok: 'sum(rate(fission_function_calls_total{status="success"}[5m]))',
  all: 'sum(rate(fission_function_calls_total[5m]))',
};

function fmtS(n: number | null): string {
  if (n === null) return '—';
  return n < 1 ? (n * 1000).toFixed(0) + 'ms' : n.toFixed(3) + 's';
}

function MetricCell({ label, query, kind, denomQuery }: { label: string; query: string; kind: 's' | 'n' | 'pct'; denomQuery?: string }): ReactElement {
  const q = useProm(query);
  const dq = useProm(denomQuery ?? null);
  // 查询被拒 / 无数据时显示短因
  const reason = q.error ? promErrorReason(q.error) : denomQuery && dq.error ? promErrorReason(dq.error) : null;
  if (reason) {
    return (
      <div className="count-cell">
        <div className="n" style={{ color: 'var(--gray-teal)' }}>N/A</div>
        <div className="l">{label} · {reason}</div>
      </div>
    );
  }
  let shown: string;
  let has = promScalar(q.data) !== null;
  if (kind === 's') {
    shown = fmtS(promScalar(q.data));
  } else if (kind === 'pct') {
    const okRate = promScalar(q.data);
    const allRate = promScalar(dq.data);
    has = okRate !== null && allRate !== null && allRate > 0;
    shown = has ? ((okRate! / allRate!) * 100).toFixed(1) + '%' : '—';
  } else {
    const v = promScalar(q.data);
    shown = v === null ? '—' : v.toFixed(2);
  }
  return (
    <div className="count-cell">
      <div className="n" style={{ color: has ? 'var(--ink)' : 'var(--gray-teal)' }}>{shown}</div>
      <div className="l">{label}{has ? '' : ' · 无数据'}</div>
    </div>
  );
}

function GuidePanel({ probeErr }: { probeErr: string | null }): ReactElement {
  return (
    <main className="page page-pad">
      <div className="pane-h">网格与观测<span>MESH · 未检测到服务网格</span></div>
      {probeErr ? <div className="banner">▲ 网格探测失败：{probeErr}</div> : (
        <div className="banner" style={{ marginBottom: 16 }}>▲ 未检测到 Istio / Linkerd 服务网格。以下为各网格一行安装指引，安装后本页自动进入指标面板。</div>
      )}
      <table className="plain" style={{ maxWidth: 760 }}>
        <thead><tr><th>网格</th><th>安装指引</th><th>说明</th></tr></thead>
        <tbody>
          <tr>
            <td className="fn-name">Istio</td>
            <td className="mono" style={{ fontSize: 11 }}>istioctl install --set profile=demo</td>
            <td style={{ whiteSpace: 'normal' }}>默认 profile 即可支撑 Fission 函数网格观测。</td>
          </tr>
          <tr>
            <td className="fn-name">Linkerd</td>
            <td className="mono" style={{ fontSize: 11 }}>linkerd install | kubectl apply -f -</td>
            <td style={{ whiteSpace: 'normal' }}>先安装 linkerd CRDs：linkerd install --crds | kubectl apply -f -。</td>
          </tr>
        </tbody>
      </table>
    </main>
  );
}

export default function Mesh(): ReactElement {
  const probe = useMeshProbe();

  if (probe.isLoading) {
    return <main className="page page-pad"><div className="empty">探测网格中…</div></main>;
  }
  if (probe.error && errStatus(probe.error) === 403) {
    return <main className="page page-pad"><div className="banner">▲ 权限不足（403）：无 /api/mesh/probe 读取权限。</div></main>;
  }

  const p = probe.data;
  const none = !probe.error && !p?.istio && !p?.linkerd;
  if (none || (!probe.error && p?.istio === false && p?.linkerd === false)) {
    return <GuidePanel probeErr={null} />;
  }
  if (probe.error) {
    return <GuidePanel probeErr={getErrMessage(probe.error)} />;
  }

  const meshes: string[] = [];
  if (p?.istio) meshes.push('Istio');
  if (p?.linkerd) meshes.push('Linkerd');

  return (
    <main className="page">
      <div className="pane-h" style={{ margin: '14px 20px 0' }}>
        网格与观测
        <span>MESH · {meshes.join(' + ')} 指标面板</span>
      </div>

      <div className="count-band" aria-label="延迟与流量观测带">
        <MetricCell label="P50 延迟" query={Q.p50} kind="s" />
        <MetricCell label="P95 延迟" query={Q.p95} kind="s" />
        <MetricCell label="P99 延迟" query={Q.p99} kind="s" />
        <MetricCell label="RPS" query={Q.rps} kind="n" />
        <MetricCell label="成功率" query={Q.ok} kind="pct" denomQuery={Q.all} />
      </div>

      <div className="banner blue" style={{ marginBottom: 12 }}>
        ◇ 指标来自 Prometheus（<span className="mono">/api/prom</span>）。若某格显示 N/A，说明 <span className="mono">PROMETHEUS_URL</span> 未配置或对应指标无样本——这不影响其余面板。
      </div>

      <div style={{ margin: '0 20px 20px' }}>
        <div className="obs-card" style={{ maxWidth: 760 }}>
          <h3>入口规则边界说明</h3>
          <p style={{ fontSize: 13, lineHeight: 1.8 }}>
            本页仅做只读观测，<b>不提供</b> VirtualService 或通用 Istio YAML 编辑。函数入口路由（Host / 路径 / 方法 / Ingress）请在
            「HTTP 触发器」页编辑——它就是 Fission 的入口规则来源，网格侧配置请交由平台团队通过 Git 管理。
          </p>
        </div>
      </div>
    </main>
  );
}

export function MeshProbeBadge(): ReactElement {
  const probe = useMeshProbe();
  if (probe.isLoading) return <StatusBadge status="探测中" tone="neutral" />;
  const p = probe.data;
  if (p?.istio) return <StatusBadge status="ISTIO" tone="ok" />;
  if (p?.linkerd) return <StatusBadge status="LINKERD" tone="ok" />;
  return <StatusBadge status="无网格" tone="neutral" />;
}