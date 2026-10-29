import { useEffect, useState, type ReactElement } from 'react';
import { api } from '../api/client';
import { useList, useWatch } from '../api/hooks';
import type { Environment } from '../api/types';
import { DataTable, useToast } from '../components';
import type { Column } from '../components';

/** Admin gate: members of fission-admins see the write actions (P1). */
export function useIsAdmin(): boolean {
  const [admin, setAdmin] = useState(false);
  useEffect(() => {
    let alive = true;
    api
      .get<{ groups?: string[] }>('/whoami')
      .then((who) => {
        if (alive) setAdmin((who.groups ?? []).includes('fission-admins'));
      })
      .catch(() => {
        // whoami not deployed — default to read-only posture
        if (alive) setAdmin(false);
      });
    return () => {
      alive = false;
    };
  }, []);
  return admin;
}

export default function Environments(): ReactElement {
  const envs = useList<Environment>('environments');
  useWatch('environments');
  const toast = useToast();
  const isAdmin = useIsAdmin();
  const [sel, setSel] = useState<Environment | null>(null);
  void toast; // 403 surface: mutations arrive in P1; errors toast via useResourceMutations then

  const columns: Column<Environment>[] = [
    {
      key: 'name',
      header: '名称',
      sortValue: (r) => r.metadata.name,
      render: (r) => (
        <div>
          <div className="fn-name">{r.metadata.name}</div>
          <div className="fn-sub mono">{r.metadata.namespace}</div>
        </div>
      ),
    },
    { key: 'version', header: '版本', sortValue: (r) => r.spec.version, render: (r) => <span className="num">v{r.spec.version}</span> },
    { key: 'image', header: '运行时镜像', sortValue: (r) => r.spec.runtime?.image ?? r.spec.image ?? '', render: (r) => <span className="mono" style={{ fontSize: 11 }}>{r.spec.runtime?.image ?? r.spec.image ?? '—'}</span> },
    { key: 'poolsize', header: '池尺寸', sortValue: (r) => r.spec.poolsize ?? 0, render: (r) => <span className="num">{r.spec.poolsize ?? '—'}</span> },
  ];

  return (
    <div className="shell" style={{ flex: 1, minHeight: 0 }}>
      <div className="list-pane" style={{ width: 'auto', flex: '1 1 460px' }}>
        <div className="pane-h">
          环境观测表
          <span>{envs.data?.length ?? 0} · 只读{isAdmin ? '' : ' · 非管理员组'}</span>
        </div>
        <div className="list-scroll">
          {envs.isError ? (
            <div className="banner">▲ 环境列表不可达：{envs.error instanceof Error ? envs.error.message : ''}</div>
          ) : (
            <DataTable
              columns={columns}
              rows={envs.data ?? []}
              rowKey={(r) => r.metadata.uid ?? r.metadata.name}
              onRowClick={setSel}
              initialSort={{ key: 'name', dir: 'asc' }}
            />
          )}
        </div>
      </div>
      <section className="inspector">
        {sel ? (
          <>
            <div className="ins-h">
              <h2>{sel.metadata.name}</h2>
              <span className="mono muted" style={{ fontSize: 12 }}>v{sel.spec.version}</span>
            </div>
            <p className="meta">
              命名空间 <b className="mono">{sel.metadata.namespace}</b>　外部网络 <b>{sel.spec.allowAccessToExternalNetwork ? '允许' : '禁止'}</b>
            </p>
            <div className="obs-card">
              <h3>SPEC</h3>
              <table className="plain">
                <tbody>
                  <tr><th>runtime.image</th><td className="mono">{sel.spec.runtime?.image ?? sel.spec.image ?? '—'}</td></tr>
                  <tr><th>builder.image</th><td className="mono">{sel.spec.builder?.image ?? '—'}</td></tr>
                  <tr><th>poolsize</th><td className="num">{sel.spec.poolsize ?? '—'}</td></tr>
                  <tr><th>imagepullsecret</th><td className="mono">{sel.spec.imagepullsecret ?? '—'}</td></tr>
                </tbody>
              </table>
            </div>
            {isAdmin ? (
              <div className="cap">管理员组可用 — 环境写操作将在 P1 提供表单与 YAML 编辑。</div>
            ) : (
              <div className="banner blue">◇ 只读视图：写操作对非 fission-admins 组隐藏；后端仍执行 RBAC 预检，拒绝时 toast 回流明确 message。</div>
            )}
          </>
        ) : (
          <div className="empty" style={{ marginTop: 64 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 18, letterSpacing: 1, marginBottom: 8 }}>选择一个环境</div>
            <p>查看运行时镜像、builder 与池配置。</p>
          </div>
        )}
      </section>
    </div>
  );
}
