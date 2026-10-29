import { useEffect, useMemo, useState, type ReactElement } from 'react';
import { useList } from '../api/hooks';
import { useFunctionVersionList, useRollback } from '../api/extend';
import type { VersionInfo } from '../api/extend';
import type { Function } from '../api/types';
import { ConfirmDialog, StatusBadge, useToast } from '../components';
import { getErrMessage, errStatus } from '../lib/err';
import { sortVersions, fmtVersionTime, COLD_START_WARNING } from '../lib/versions';

type F = Function & { kind: 'Function' };

function VersionRow({ v, aliasTarget, aliasName, onRollback, latest }: {
  v: VersionInfo;
  aliasTarget: string;
  aliasName: string;
  onRollback: (v: VersionInfo) => void;
  latest: boolean;
}): ReactElement {
  const isAlias = v.name === aliasTarget;
  return (
    <li style={{ listStyle: 'none', position: 'relative', paddingLeft: 26, paddingBottom: 14 }}>
      <span
        aria-hidden="true"
        className="mono"
        style={{
          position: 'absolute', left: 0, top: 3,
          width: 10, height: 10, borderRadius: isAlias ? '50%' : '1px',
          background: isAlias ? 'var(--obs)' : 'var(--hairline-strong)',
          border: '2px solid ' + (isAlias ? 'var(--obs-soft)' : 'var(--paper)'),
          boxShadow: isAlias ? '0 0 0 2px var(--obs)' : 'none',
        }}
      />
      <span aria-hidden="true" style={{ position: 'absolute', left: 4, top: 16, bottom: -2, width: 1, background: 'var(--row-line)' }} />
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span className={'mono' + (isAlias ? ' fn-name' : '')} style={{ color: isAlias ? 'var(--obs)' : 'var(--ink)' }}>{v.name}</span>
        {isAlias ? <StatusBadge status={'别名 ' + aliasName} qualifier="当前指向" /> : null}
        {latest && !isAlias ? <span className="status status-neutral"><span className="sig sig-dot" />最新</span> : null}
        <span className="num muted" style={{ fontSize: 11 }}>{fmtVersionTime(v.created, v.creationTimestamp)}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <StatusBadge
            status={v.functionExists ? 'FUNCTION OK' : 'FUNCTION 缺失'}
            tone={v.functionExists ? 'ok' : 'error'}
          />
          <button
            type="button"
            className="btn sm"
            disabled={isAlias}
            title={isAlias ? '当前别名已指向该版本' : '回滚到 ' + v.name}
            onClick={() => onRollback(v)}
          >
            回滚到此版
          </button>
        </span>
      </div>
    </li>
  );
}

export default function Versions(): ReactElement {
  const toast = useToast();
  const functions = useList<F>('functions');
  const [sel, setSel] = useState<{ ns: string; name: string } | null>(null);
  const [confirmVer, setConfirmVer] = useState<VersionInfo | null>(null);

  useEffect(() => {
    if (!sel && functions.data?.length) {
      const f = functions.data[0];
      setSel({ ns: f.metadata.namespace, name: f.metadata.name });
    }
  }, [functions.data, sel]);

  const versions = useFunctionVersionList(sel?.ns ?? '', sel?.name ?? '');
  const rollback = useRollback(sel?.ns ?? '', sel?.name ?? '');

  const alias = versions.data?.aliases?.[0] ?? null;

  const ordered = useMemo(() => sortVersions(versions.data?.versions ?? []), [versions.data]);

  const vErr = versions.error;
  const unavailable = errStatus(vErr) === 501 || errStatus(vErr) === 404;

  const doRollback = (): void => {
    if (!confirmVer || !alias || !sel) return;
    rollback.mutate(
      { alias: alias.name, toVersion: confirmVer.name },
      {
        onSuccess: (res) => {
          setConfirmVer(null);
          toast.show('回滚 ' + sel.name, res.coldStartWarning ? String(res.coldStartWarning) : '已回滚到 ' + confirmVer.name);
        },
        onError: (err) => toast.showError('回滚 ' + sel.name, getErrMessage(err)),
      },
    );
  };

  const fns = functions.data ?? [];

  return (
    <main className="page page-pad">
      <div className="pane-h">
        版本与发布
        <span>VERSIONS · 别名指向高亮 · 回滚含冷启动警示</span>
      </div>

      {functions.isError ? (
        <div className="banner">▲ 函数列表不可达：{getErrMessage(functions.error)}</div>
      ) : fns.length === 0 ? (
        <div className="empty">尚无函数观测点 — 先在「函数」页创建。</div>
      ) : (
        <>
          <div className="form-row" style={{ maxWidth: 640 }}>
            <div className="field">
              <span>函数选择器</span>
              <select
                value={sel ? sel.ns + '/' + sel.name : ''}
                onChange={(e) => {
                  const [ns, ...rest] = e.target.value.split('/');
                  setSel({ ns, name: rest.join('/') });
                }}
              >
                {fns.map((f) => (
                  <option key={f.metadata.namespace + '/' + f.metadata.name} value={f.metadata.namespace + '/' + f.metadata.name}>
                    {f.metadata.namespace}/{f.metadata.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {vErr && !unavailable ? (
            <div className="banner">▲ 版本端点不可达（{errStatus(vErr) ?? '?'}）：{getErrMessage(vErr)}</div>
          ) : null}
          {unavailable ? (
            <div className="banner blue">◇ 版本端点未上线（501）——后端 versions 路由启用后此页自动生效。</div>
          ) : null}

          <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', flexWrap: 'wrap', marginTop: 8 }}>
            <section style={{ flex: '1 1 460px', minWidth: 0 }}>
              <div className="pane-h" style={{ paddingLeft: 0 }}>
                版本时间线
                <span>{ordered.length} 个版本{alias ? ' · 别名 ' + alias.name + ' → ' + alias.target : ''}</span>
              </div>
              {versions.isLoading ? (
                <div className="empty">加载中…</div>
              ) : ordered.length === 0 ? (
                <div className="empty">暂无版本记录</div>
              ) : (
                <ul style={{ margin: '4px 0 0', padding: 0 }}>
                  {ordered.map((v, i) => (
                    <VersionRow
                      key={v.name}
                      v={v}
                      latest={i === 0}
                      aliasTarget={alias?.target ?? ''}
                      aliasName={alias?.name ?? 'latest'}
                      onRollback={setConfirmVer}
                    />
                  ))}
                </ul>
              )}
            </section>

            <section style={{ flex: '0 1 320px' }}>
              <div className="obs-card">
                <h3>别名登记簿</h3>
                {(versions.data?.aliases ?? []).length === 0 ? (
                  <div className="cap">无别名记录</div>
                ) : (
                  <table className="plain">
                    <thead><tr><th>别名</th><th>指向</th></tr></thead>
                    <tbody>
                      {(versions.data?.aliases ?? []).map((a) => (
                        <tr key={a.name}>
                          <td className="mono fn-name">{a.name}</td>
                          <td className="mono" style={{ color: 'var(--obs)' }}>{a.target}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div className="cap">回滚通过别名重指向完成；新副本冷启动期间首次调用延迟上升，属正常现象。</div>
            </section>
          </div>
        </>
      )}

      <ConfirmDialog
        open={confirmVer !== null}
        title={'回滚 · ' + (confirmVer?.name ?? '')}
        message={
          '将把别名 ' + (alias?.name ?? 'latest') + ' 重指向 ' + (confirmVer?.name ?? '') + '。' +
          COLD_START_WARNING
        }
        confirmName={confirmVer?.name}
        confirmLabel="确认回滚"
        onConfirm={doRollback}
        onCancel={() => setConfirmVer(null)}
      />
    </main>
  );
}
