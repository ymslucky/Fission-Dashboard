import { useMemo, useState, type ReactElement } from 'react';
import { useList, useResourceMutations, useWatch } from '../api/hooks';
import type { Package } from '../api/types';
import { DataTable, StatusBadge, useToast } from '../components';
import type { Column } from '../components';

const BUILD_STATUS: Record<string, { text: string; tone: 'ok' | 'pending' | 'error' | 'neutral' }> = {
  pending: { text: 'PENDING', tone: 'pending' },
  build: { text: 'BUILDING', tone: 'pending' },
  running: { text: 'RUNNING', tone: 'pending' },
  none: { text: 'NONE', tone: 'neutral' },
  succeeded: { text: 'SUCCEEDED', tone: 'ok' },
  failed: { text: 'FAILED', tone: 'error' },
};

function buildOf(p: Package): string {
  // 对齐 functions_ext.py：构建状态在 status.buildstatus.{status|buildlog}；字符串形态兜底
  const bs = (p as unknown as { status?: { buildstatus?: { status?: string } | string } }).status?.buildstatus;
  const s = typeof bs === 'string' ? bs : bs?.status;
  return String(s ?? 'none').toLowerCase();
}

function literalSources(p: Package): string | null {
  const lit = p.spec.source?.literal ?? p.spec.deployment?.literal;
  if (!lit || Object.keys(lit).length === 0) return null;
  return Object.entries(lit).map(([k, v]) => '--- ' + k + ' ---\n' + String(v)).join('\n\n');
}

export default function Packages(): ReactElement {
  const packages = useList<Package>('packages');
  useWatch('packages');
  const muts = useResourceMutations<Package>('packages');
  const toast = useToast();
  const [sel, setSel] = useState<Package | null>(null);

  const columns: Column<Package>[] = [
    {
      key: 'name',
      header: '名称',
      sortValue: (r) => r.metadata.name,
      render: (r) => (
        <div>
          <div className="fn-name">{r.metadata.name}</div>
          <div className="fn-sub mono">{r.metadata.namespace} · env {r.spec.environment?.name}</div>
        </div>
      ),
    },
    {
      key: 'build',
      header: '构建状态',
      sortValue: (r) => buildOf(r),
      render: (r) => {
        const b = BUILD_STATUS[buildOf(r)] ?? { text: buildOf(r).toUpperCase(), tone: 'neutral' as const };
        return <StatusBadge status={b.text} tone={b.tone} />;
      },
    },
    {
      key: 'created',
      header: '创建时间',
      sortValue: (r) => r.metadata.creationTimestamp ?? '',
      render: (r) => <span className="num">{r.metadata.creationTimestamp ?? '—'}</span>,
    },
  ];

  const rebuild = (p: Package): void => {
    muts.update.mutate(
      { ns: p.metadata.namespace, name: p.metadata.name, body: p },
      {
        onSuccess: () => toast.show('重新构建', p.metadata.name + ' 构建已触发'),
        onError: (err) => toast.showError('重新构建', err),
      },
    );
  };

  const detail = useMemo(() => {
    if (!sel) return null;
    const fresh = (packages.data ?? []).find((p) => p.metadata.uid === sel.metadata.uid) ?? sel;
    return fresh;
  }, [sel, packages.data]);

  return (
    <div className="shell" style={{ flex: 1, minHeight: 0 }}>
      <div className="list-pane" style={{ width: 'auto', flex: '1 1 460px' }}>
        <div className="pane-h">包观测表<span>{packages.data?.length ?? 0} · 构建状态冗余编码</span></div>
        <div className="list-scroll">
          {packages.isError ? (
            <div className="banner">▲ 包列表不可达：{packages.error instanceof Error ? packages.error.message : ''}</div>
          ) : (
            <DataTable
              columns={columns}
              rows={packages.data ?? []}
              rowKey={(r) => r.metadata.uid ?? r.metadata.name}
              onRowClick={setSel}
              initialSort={{ key: 'build', dir: 'asc' }}
              rowClass={(r) => (buildOf(r) === 'failed' ? 'row-alert' : undefined)}
            />
          )}
        </div>
      </div>
      <section className="inspector">
        {detail ? (
          <>
            <div className="ins-h">
              <h2>{detail.metadata.name}</h2>
              <StatusBadge status={(BUILD_STATUS[buildOf(detail)] ?? { text: buildOf(detail).toUpperCase() }).text} tone={(BUILD_STATUS[buildOf(detail)] ?? { tone: 'neutral' as const }).tone} />
              <button type="button" className="btn sm" style={{ marginLeft: 'auto' }} disabled={muts.update.isPending} onClick={() => rebuild(detail)}>
                重新构建
              </button>
            </div>
            <p className="meta">
              环境 <b>{detail.spec.environment?.name}</b>　命名空间 <b className="mono">{detail.metadata.namespace}</b>
              {detail.spec.deployment?.checksum ? <>　deployment <b className="mono">sha256:{Object.values(detail.spec.deployment.checksum)[0]?.slice(0, 12) ?? '…'}</b></> : null}
            </p>
            <div className="obs-card">
              <h3>构建日志</h3>
              <div className="cap">后端构建日志端点扩展中 — 当前展示包状态与内联源码。</div>
              <div className="mono" style={{ fontSize: 11, marginTop: 6, whiteSpace: 'pre-wrap', color: 'var(--ink-soft)' }}>
                {buildOf(detail) === 'failed' ? '▲ 构建失败 — 请检查环境 builder 镜像与源码。' : buildOf(detail) === 'succeeded' ? '● 构建成功。' : '○ 构建状态：' + buildOf(detail)}
              </div>
            </div>
            <div className="obs-card">
              <h3>源码查看</h3>
              {literalSources(detail) ? (
                <pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--ink)', color: '#c9d4d8', padding: '8px 10px', maxHeight: 260, overflow: 'auto' }}>
                  {literalSources(detail)}
                </pre>
              ) : (
                <div className="cap">非内联源码（归档上传）— 源码查看需后端归档端点支持。</div>
              )}
            </div>
          </>
        ) : (
          <div className="empty" style={{ marginTop: 64 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 18, letterSpacing: 1, marginBottom: 8 }}>选择一个包</div>
            <p>查看构建状态、内联源码与重新构建。</p>
          </div>
        )}
      </section>
    </div>
  );
}
