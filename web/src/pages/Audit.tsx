import { useMemo, useState, type ReactElement } from 'react';
import { useList } from '../api/hooks';
import { useAudit } from '../api/extend';
import { DataTable, KindDot, useToast } from '../components';
import type { Column } from '../components';
import { getErrMessage, errStatus } from '../lib/err';

interface AuditRow {
  ts: string;
  user: string;
  action: string;
  resource: string;
  summary: string;
}

const LIMITS = [50, 200, 1000] as const;
const FN_NS = 'fission';

function fmtTs(ts?: string): string {
  if (!ts) return '—';
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString('zh-CN', { hour12: false });
}

function AuditTable(): ReactElement {
  const toast = useToast();
  const [limit, setLimit] = useState<number>(200);
  const audit = useAudit(limit);

  if (audit.isError && errStatus(audit.error) === 403) {
    return <div className="banner">▲ 权限不足（403）：当前账号无审计读取权限。</div>;
  }
  if (audit.isError && (errStatus(audit.error) === 501 || errStatus(audit.error) === 404)) {
    return <div className="banner blue">◇ 审计端点未上线（501）——后端启用后此表自动填充。</div>;
  }
  if (audit.isError) {
    const msg = getErrMessage(audit.error);
    return (
      <div>
        <div className="banner">▲ 审计流水不可达：{msg}</div>
        <button type="button" className="btn sm" style={{ marginTop: 8 }} onClick={() => { toast.showError('审计', msg); void audit.refetch(); }}>重试</button>
      </div>
    );
  }

  const rows: AuditRow[] = (audit.data ?? []).map((e, i) => ({
    ts: fmtTs(String(e.ts ?? e.timestamp ?? '')) + '#' + i,
    user: e.user ?? '—',
    action: e.action ?? '—',
    // BE (audit.py) 实有字段 kind/ns/name，拼出资源列
    resource: [e.kind, (e.ns ? e.ns + '/' : '') + (e.name ?? '')].filter((x) => x).join(' ') || '—',
    summary: e.summary ?? '',
  }));

  const columns: Column<AuditRow>[] = [
    { key: 'ts', header: '时间', sortValue: (r) => r.ts, render: (r) => <span className="num muted" style={{ fontSize: 11 }}>{r.ts.split('#')[0]}</span>, width: 180 },
    { key: 'user', header: '用户', sortValue: (r) => r.user, render: (r) => <span className="mono">{r.user}</span> },
    { key: 'action', header: '动作', sortValue: (r) => r.action, render: (r) => <span className="mono">{r.action}</span> },
    { key: 'resource', header: '资源', sortValue: (r) => r.resource, render: (r) => <span className="mono" style={{ fontSize: 11 }}>{r.resource}</span> },
    { key: 'summary', header: '摘要', render: (r) => <span style={{ whiteSpace: 'normal' }}>{r.summary}</span> },
  ];

  return (
    <>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 6 }}>
        <div className="field" style={{ marginBottom: 0, width: 140 }}>
          <span>条数上限</span>
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {LIMITS.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </div>
        <span className="cap">{rows.length} 条 · 最新在上</span>
      </div>
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.ts} initialSort={{ key: 'ts', dir: 'desc' }} emptyText="暂无审计记录" />
    </>
  );
}

function TenantCounts(): ReactElement {
  const kinds = ['functions', 'packages', 'environments', 'httptriggers', 'timetriggers'] as const;
  const labels: Record<string, { zh: string; dot: string }> = {
    functions: { zh: '函数', dot: 'var(--kind-fn)' },
    packages: { zh: '包', dot: 'var(--kind-pkg)' },
    environments: { zh: '环境', dot: 'var(--kind-env)' },
    httptriggers: { zh: 'HTTP 触发器', dot: 'var(--kind-http)' },
    timetriggers: { zh: '定时触发器', dot: 'var(--kind-timer)' },
  };

  const lists = [
    useList('functions', FN_NS),
    useList('packages', FN_NS),
    useList('environments', FN_NS),
    useList('httptriggers', FN_NS),
    useList('timetriggers', FN_NS),
  ];

  const rows = useMemo(
    () =>
      kinds.map((k, i) => ({
        kind: k,
        zh: labels[k].zh,
        dot: labels[k].dot,
        n: lists[i].data?.length ?? null,
        loading: lists[i].isLoading,
        denied: lists[i].error !== null && !(errStatus(lists[i].error) === 501 || errStatus(lists[i].error) === 404),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lists.map((l) => [l.data, l.isLoading, l.error]).join('|')],
  );

  const total = rows.reduce((a, r) => a + (r.n ?? 0), 0);

  return (
    <section style={{ marginTop: 24 }}>
      <div className="pane-h" style={{ paddingLeft: 0 }}>
        租户视图 · {FN_NS} 命名空间
        <span>资源计数聚合 · 共 {total} 个对象</span>
      </div>
      <table className="obs-table">
        <thead><tr><th>资源类型</th><th>计数</th><th>占比</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.kind}>
              <td>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <KindDot kind={r.kind === 'functions' ? 'functions' : r.kind === 'packages' ? 'packages' : r.kind === 'environments' ? 'environments' : r.kind === 'httptriggers' ? 'httptriggers' : 'timetriggers'} />
                  {r.zh}
                </span>
              </td>
              <td className="num">{r.denied ? <span className="muted">无权限</span> : r.loading ? '…' : String(r.n ?? 0).padStart(2, '0')}</td>
              <td className="num muted">{total > 0 && r.n ? (r.n / total * 100).toFixed(0) + '%' : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="cap" style={{ marginTop: 6 }}>统计范围：{FN_NS} 命名空间下的 Fission CRD 对象。</div>
    </section>
  );
}

export default function Audit(): ReactElement {
  return (
    <main className="page page-pad">
      <div className="pane-h">租户与审计<span>AUDIT · 流水只读 · 紧排等宽</span></div>
      <AuditTable />
      <TenantCounts />
    </main>
  );
}
