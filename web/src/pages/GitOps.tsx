import { useState, type ReactElement } from 'react';
import { useList } from '../api/hooks';
import {
  useGitopsApply,
  useGitopsExport,
  useGitopsFile,
  useGitopsPlan,
  useGitopsStatus,
  useGitopsTree,
} from '../api/extend';
import type { GitopsPlanItem, GitopsResourceRow } from '../api/extend';
import type { Function } from '../api/types';
import { ConfirmDialog, StatusBadge, useToast } from '../components';
import { getErrMessage, errStatus } from '../lib/err';
import type { StatusTone } from '../components';

type F = Function & { kind: 'Function' };

const STATE_TONE: Record<string, StatusTone> = {
  synced: 'ok',
  'repo-only': 'pending',
  'repo-changed': 'pending',
  'cluster-changed': 'pending',
  conflict: 'error',
};

const STATE_TEXT: Record<string, string> = {
  synced: 'SYNCED 已同步',
  'repo-only': 'REPO-ONLY 仅仓库',
  'repo-changed': 'REPO-CHANGED 仓库有变',
  'cluster-changed': 'CLUSTER-CHANGED 集群有变',
  conflict: 'CONFLICT 冲突',
};

function StateBadge({ state }: { state?: string }): ReactElement {
  const s = state ?? 'unknown';
  return <StatusBadge status={STATE_TEXT[s] ?? s} tone={STATE_TONE[s] ?? 'neutral'} />;
}

/** 计数观测带（与 Overview count-band 同视觉词汇） */
function CountCell({ label, n, alert }: { label: string; n: number | undefined; alert?: boolean }): ReactElement {
  return (
    <div className="count-cell">
      <div className="n" style={{ color: alert && n ? 'var(--alarm)' : typeof n === 'number' && n > 0 ? 'var(--ink)' : 'var(--gray-teal)' }}>
        {typeof n === 'number' ? String(n).padStart(2, '0') : '—'}
      </div>
      <div className="l">{label}</div>
    </div>
  );
}

function resLabel(i: GitopsPlanItem | GitopsResourceRow): string {
  return (i.kind ? i.kind + ' ' : '') + (i.ns ? i.ns + '/' : '') + (i.name ?? '(未命名)');
}

export default function GitOps(): ReactElement {
  const toast = useToast();
  const [src, setSrc] = useState('repo');
  const status = useGitopsStatus(src);
  const tree = useGitopsTree(src);
  const plan = useGitopsPlan(src);
  const [file, setFile] = useState<string | null>(null);
  const fileQ = useGitopsFile(src, file);
  const apply = useGitopsApply(src);
  const exp = useGitopsExport(src);

  const [conflictChoice, setConflictChoice] = useState<Record<string, 'repo' | 'cluster'>>({});
  const [deleteSel, setDeleteSel] = useState<string[]>([]);
  const [applyOpen, setApplyOpen] = useState(false);
  const [gateOpen, setGateOpen] = useState(false);
  const [exportSel, setExportSel] = useState<string[]>([]);
  const [exportMode, setExportMode] = useState('export');
  const [exportResult, setExportResult] = useState<string | null>(null);

  const functions = useList<F>('functions');
  const planOk = plan.isSuccess && !plan.isFetching;

  const conflicts = plan.data?.conflict ?? [];
  const toCreate = plan.data?.create ?? [];
  const toUpdate = plan.data?.update ?? [];
  const toDelete = plan.data?.delete ?? [];
  const unchanged = plan.data?.unchanged ?? [];
  const changeCount = toCreate.length + toUpdate.length + toDelete.length + conflicts.length;
  const allConflictsResolved = conflicts.every((c) => c.key && (conflictChoice[c.key] === 'repo' || conflictChoice[c.key] === 'cluster'));
  const canApply = planOk && changeCount > 0 && allConflictsResolved;

  const resources = tree.data?.resources ?? [];
  const files = tree.data?.files ?? [];
  const counts = status.data;
  const conflictCount = counts?.conflict ?? 0;

  const resetPlanChoices = (): void => { setConflictChoice({}); setDeleteSel([]); };

  const doApply = (): void => {
    apply.mutate(
      // ApplyPayload（misc.py）：deletes=要删除的资源 key 列表；conflicts={key:'repo'|'cluster'}
      { deletes: deleteSel, conflicts: conflictChoice },
      {
        onSuccess: () => { toast.show('GitOps 应用', 'apply 已提交'); setApplyOpen(false); resetPlanChoices(); void plan.refetch(); void status.refetch(); void tree.refetch(); },
        onError: (err) => { toast.showError('GitOps 应用', getErrMessage(err)); setApplyOpen(false); },
      },
    );
  };

  const doExport = (): void => {
    if (exportSel.length === 0) { toast.show('GitOps 导出', '至少选择一个函数', 'error'); return; }
    exp.mutate(
      { mode: exportMode, resources: exportSel },
      {
        onSuccess: (res) => {
          setExportResult(res.prUrl ?? res.pr ?? res.message ?? '导出完成');
          toast.show('GitOps 导出', res.prUrl ?? '导出完成');
        },
        onError: (err) => toast.showError('GitOps 导出', getErrMessage(err)),
      },
    );
  };

  const gate = status.isError && (errStatus(status.error) === 501 || errStatus(status.error) === 404);

  const conflictRadios = (item: GitopsPlanItem): ReactElement => (
    <div style={{ display: 'flex', gap: 12, fontSize: 12 }}>
      <label style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <input type="radio" name={'cf-' + item.key} checked={item.key != null && conflictChoice[item.key] === 'repo'} onChange={() => item.key && setConflictChoice((o) => ({ ...o, [item.key!]: 'repo' }))} />
        以仓库为准
      </label>
      <label style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <input type="radio" name={'cf-' + item.key} checked={item.key != null && conflictChoice[item.key] === 'cluster'} onChange={() => item.key && setConflictChoice((o) => ({ ...o, [item.key!]: 'cluster' }))} />
        以集群为准
      </label>
    </div>
  );

  return (
    <main className="page page-pad">
      <div className="pane-h">
        GitOps 同步
        <span>GITOPS · 先 plan 后 apply · 冲突强制选边</span>
        <span className="actions">
          <span className="field" style={{ marginBottom: 0 }}>
            <select value={src} onChange={(e) => { setSrc(e.target.value); resetPlanChoices(); setFile(null); }} aria-label="源">
              <option value="repo">repo（默认源）</option>
            </select>
          </span>
        </span>
      </div>

      {gate ? (
        <div className="banner blue">◇ GitOps 端点未上线（501）——配置 Git 仓库源后此页自动生效。</div>
      ) : null}
      {status.isError && errStatus(status.error) === 403 ? (
        <div className="banner">▲ 权限不足（403）：无 /api/gitops 读取权限。</div>
      ) : null}
      {status.isError && !gate && errStatus(status.error) !== 403 ? (
        <div className="banner">▲ GitOps 状态不可达：{getErrMessage(status.error)}</div>
      ) : null}

      {/* 同步状态：五格计数观测带 */}
      <div className="count-band" aria-label="GitOps 同步计数观测带">
        <CountCell label="新建 CREATE" n={counts?.create} />
        <CountCell label="更新 UPDATE" n={counts?.update} />
        <CountCell label="删除 DELETE" n={counts?.delete} />
        <CountCell label="冲突 CONFLICT" n={counts?.conflict} alert />
        <CountCell label="一致 UNCHANGED" n={counts?.unchanged} />
      </div>
      {conflictCount > 0 ? (
        <div className="banner" style={{ marginBottom: 12 }}>▲ 检测到 {conflictCount} 个冲突 —— 请在下方「计划 · 差异预览」区为每个冲突选择以仓库/以集群为准。</div>
      ) : null}

      <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* 资源树表（tree.resources） */}
        <section style={{ flex: '1 1 520px', minWidth: 0 }}>
          <div className="pane-h" style={{ paddingLeft: 0 }}>资源树表<span>{resources.length} 资源</span></div>
          {tree.isError ? <div className="cap">▲ tree 不可达：{getErrMessage(tree.error)}</div> : null}
          {tree.isLoading ? <div className="empty">加载中…</div> : resources.length === 0 ? (
            <div className="empty">暂无受管资源</div>
          ) : (
            <table className="obs-table">
              <thead><tr><th>资源</th><th>状态</th><th>路径</th></tr></thead>
              <tbody>
                {resources.map((s, i) => {
                  const isConflict = s.state === 'conflict';
                  return (
                    <tr key={(s.kind ?? '') + '/' + (s.ns ?? '') + '/' + (s.name ?? '') + i} className={isConflict ? 'row-alert' : undefined} style={{ cursor: s.path ? 'pointer' : undefined }} onClick={() => s.path && setFile(s.path)}>
                      <td className="fn-name">{resLabel(s)}</td>
                      <td><StateBadge state={s.state} /></td>
                      <td className="mono" style={{ fontSize: 11 }}>{s.path ?? '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>

        {/* 文件浏览（tree.files） */}
        <section style={{ flex: '0 1 300px' }}>
          <div className="pane-h" style={{ paddingLeft: 0 }}>文件浏览<span>{files.length} files</span></div>
          {tree.isError ? <div className="cap">▲ 文件列表不可达。</div> : null}
          {tree.isLoading ? <div className="empty">加载中…</div> : files.length === 0 ? <div className="empty">暂无文件</div> : (
            <ul style={{ margin: 0, padding: 0 }}>
              {files.map((n, i) => {
                const path = n.path ?? '';
                return (
                  <li key={path + i} style={{ listStyle: 'none' }}>
                    <button
                      type="button"
                      className="btn sm"
                      style={{ border: 'none', background: file === path ? 'var(--obs-faint)' : 'transparent', color: 'var(--ink)', textAlign: 'left', width: '100%' }}
                      onClick={() => setFile(path)}
                    >
                      <span className="mono" style={{ fontSize: 11 }}>{path}{typeof n.size === 'number' ? <span className="muted"> · {n.size}B</span> : null}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      {/* 文件预览 */}
      {file ? (
        <section style={{ marginTop: 16 }}>
          <div className="pane-h" style={{ paddingLeft: 0 }}>文件预览<span className="mono">{file}</span></div>
          {fileQ.error ? <div className="cap">▲ 文件读取失败：{getErrMessage(fileQ.error)}</div> : (
            <pre className="mono" style={{ background: 'var(--paper-raise)', border: '1px solid var(--hairline)', padding: '10px 14px', fontSize: 12, overflow: 'auto', maxHeight: 320 }}>{fileQ.data?.content ?? '加载中…'}</pre>
          )}
        </section>
      ) : null}

      {/* plan 计划清单 */}
      <section style={{ marginTop: 16 }}>
        <div className="pane-h" style={{ paddingLeft: 0 }}>
          计划 · 差异预览
          <span>{plan.isFetching ? '计算中…' : plan.error ? 'plan 失败' : planOk ? '已生成' : '未生成'}</span>
          <span className="actions">
            <button type="button" className="btn sm" onClick={() => void plan.refetch()}>重新 plan</button>
            <button
              type="button"
              className="btn sm pri"
              disabled={!canApply}
              title={!planOk ? '必须先完成 plan' : changeCount === 0 ? 'plan 无变更，无需应用' : !allConflictsResolved ? '存在未选边的冲突' : undefined}
              onClick={() => (allConflictsResolved ? setApplyOpen(true) : setGateOpen(true))}
            >
              应用 APPLY
            </button>
          </span>
        </div>
        {plan.error ? (
          <div className="banner" style={{ margin: '0 0 10px' }}>▲ plan 失败：{getErrMessage(plan.error)}</div>
        ) : null}
        {planOk ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
            {[
              { title: '新建 CREATE', items: toCreate, extra: undefined as undefined | ((i: GitopsPlanItem) => ReactElement) },
              { title: '更新 UPDATE', items: toUpdate, extra: undefined },
              {
                title: '删除 DELETE（勾选确认）',
                items: toDelete,
                extra: (i: GitopsPlanItem) => (
                  <label style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12 }}>
                    <input
                      type="checkbox"
                      checked={i.key != null && deleteSel.includes(i.key)}
                      onChange={(e) => i.key && setDeleteSel((old) => (e.target.checked ? [...old, i.key!] : old.filter((x) => x !== i.key)))}
                    />
                    纳入删除
                  </label>
                ),
              },
              { title: '冲突 CONFLICT', items: conflicts, extra: conflictRadios },
            ].map((g) => (
              <div key={g.title} style={{ border: '1px solid var(--hairline)', background: 'var(--paper-raise)', padding: '8px 12px', minWidth: 0 }}>
                <div className="cap" style={{ marginBottom: 4 }}>{g.title} · {g.items.length}</div>
                {g.items.length === 0 ? <div className="cap muted">—</div> : g.items.map((i) => (
                  <div key={i.key ?? resLabel(i)} style={{ borderTop: '1px solid var(--hairline)', padding: '6px 0', fontSize: 12 }}>
                    <div className="fn-name mono" style={{ fontSize: 12 }}>{resLabel(i)}</div>
                    <div className="mono muted" style={{ fontSize: 11 }}>{i.path ?? '—'}{'hint' in i && i.hint ? ' · ' + i.hint : ''}</div>
                    {g.extra ? g.extra(i) : null}
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : (
          <div className="cap">应用按钮在 plan 成功且存在变更时解锁；冲突需先选边，删除需逐条勾选确认。</div>
        )}
        {planOk && unchanged.length > 0 ? (
          <div className="cap" style={{ marginTop: 8 }}>另有 {unchanged.length} 个资源仓库与集群一致，不参与应用。</div>
        ) : null}
      </section>

      {/* 导出 */}
      <section style={{ marginTop: 20 }}>
        <div className="pane-h" style={{ paddingLeft: 0 }}>导出 EXPORT<span>选函数 + 依赖 → 生成 PR</span></div>
        <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div className="obs-card" style={{ margin: 0, flex: '1 1 380px' }}>
            <h3>选择资源</h3>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {(functions.data ?? []).map((f) => {
                const key = f.metadata.namespace + '/' + f.metadata.name;
                return (
                  <label key={key} style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12 }}>
                    <input
                      type="checkbox"
                      checked={exportSel.includes(key)}
                      onChange={(e) => setExportSel((old) => (e.target.checked ? [...old, key] : old.filter((x) => x !== key)))}
                    />
                    <span className="mono">{key}</span>
                  </label>
                );
              })}
              {(functions.data ?? []).length === 0 ? <span className="cap">无函数可选</span> : null}
            </div>
            <div className="cap" style={{ marginTop: 6 }}>导出将连同环境与包引用一并写入仓库。</div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="field" style={{ marginBottom: 0 }}>
              <span>模式</span>
              <select value={exportMode} onChange={(e) => setExportMode(e.target.value)}>
                <option value="export">export（生成清单 + PR）</option>
                <option value="init">init（初始化仓库）</option>
              </select>
            </div>
            <button type="button" className="btn pri" disabled={exp.isPending} onClick={doExport}>{exp.isPending ? '导出中…' : '导出'}</button>
            {exportResult ? <div className="cap" style={{ maxWidth: 380, wordBreak: 'break-all' }}>结果：{exportResult}</div> : null}
            {exp.isError ? <div className="banner" style={{ margin: 0 }}>▲ {getErrMessage(exp.error)}</div> : null}
          </div>
        </div>
      </section>

      <ConfirmDialog
        open={applyOpen}
        title="应用 APPLY"
        message={
          '将按 plan 把仓库期望写入集群：新建 ' + toCreate.length + ' · 更新 ' + toUpdate.length +
          ' · 删除 ' + deleteSel.length + '/' + toDelete.length +
          (conflicts.length ? ' · 冲突策略：' + conflicts.map((c) => resLabel(c) + '=' + (c.key ? conflictChoice[c.key] ?? '未选' : '未选')).join('，') : '') +
          '。此操作直接变更集群，请确认无误。'
        }
        confirmLabel="确认应用"
        onConfirm={doApply}
        onCancel={() => setApplyOpen(false)}
      />
      <Gate open={gateOpen} onCancel={() => setGateOpen(false)} />
    </main>
  );
}

function Gate({ open, onCancel }: { open: boolean; onCancel: () => void }): ReactElement | null {
  if (!open) return null;
  {
    return (
      <div className="overlay center" role="alertdialog" onClick={onCancel}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>
          <h3>冲突未解决</h3>
          <p className="muted" style={{ marginBottom: 12 }}>存在冲突资源尚未选择策略（以仓库为准 / 以集群为准）。请在计划区为每个冲突选择处理策略后再确认。</p>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={onCancel}>返回选择</button>
          </div>
        </div>
      </div>
    );
  }
  return null;
}
