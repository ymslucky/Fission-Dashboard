import { useState, type ReactElement } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { useList, useResourceMutations, useWatch } from '../api/hooks';
import type { TimerTrigger } from '../api/types';
import { DataTable, KindDot, Drawer, ConfirmDialog, useToast } from '../components';
import type { Column } from '../components';
import { formatDateTime, isValidCron, nextFireTimes } from '../lib/cron';

const KIND = 'timetriggers';

interface FormState {
  ns: string; name: string; cron: string; fnName: string; fnNs: string;
}

function toObject(f: FormState): TimerTrigger {
  return {
    apiVersion: 'fission.io/v1',
    kind: 'TimerTrigger',
    metadata: { name: f.name, namespace: f.ns },
    spec: {
      cron: f.cron,
      functionref: { name: f.fnName, namespace: f.fnNs || f.ns },
    },
  };
}

function CronPreview({ cron }: { cron: string }): ReactElement | null {
  if (!cron.trim()) return null;
  if (!isValidCron(cron)) {
    return <div className="banner" style={{ margin: '0 0 10px' }}>▲ cron 表达式无效 — 需要 5 字段：分 时 日 月 周，支持 * , - /</div>;
  }
  const times = nextFireTimes(cron, 3, new Date());
  return (
    <div className="obs-card" style={{ marginTop: 4 }}>
      <h3>下次触发预览</h3>
      {times.length === 0 ? (
        <div className="cap">一年内无触发时刻 — 请检查日/月/周字段。</div>
      ) : (
        <div className="mono" style={{ fontSize: 12 }}>
          {times.map((t) => <div key={t.getTime()}>{formatDateTime(t)}</div>)}
        </div>
      )}
    </div>
  );
}

export default function TimerTriggers(): ReactElement {
  const triggers = useList<TimerTrigger>(KIND);
  useWatch(KIND);
  const muts = useResourceMutations<TimerTrigger>(KIND);
  const functions = useList('functions');
  const toast = useToast();

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<TimerTrigger | null>(null);
  const [deleting, setDeleting] = useState<TimerTrigger | null>(null);
  const [form, setForm] = useState<FormState>({ ns: 'default', name: '', cron: '*/5 * * * *', fnName: '', fnNs: 'default' });
  const [editForm, setEditForm] = useState<FormState | null>(null);
  const set = (patch: Partial<FormState>): void => setForm((f) => ({ ...f, ...patch }));

  const fnNames = (functions.data ?? []).map((f) => f.metadata.name);
  const cronErr = form.cron.trim() && !isValidCron(form.cron) ? 'cron 表达式无效' : '';

  const columns: Column<TimerTrigger>[] = [
    {
      key: 'name',
      header: '名称',
      sortValue: (r) => r.metadata.name,
      render: (r) => (
        <div>
          <div className="fn-name" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <KindDot kind="timetriggers" />{r.metadata.name}
          </div>
          <div className="fn-sub mono">{r.metadata.namespace}</div>
        </div>
      ),
    },
    { key: 'cron', header: 'Cron', sortValue: (r) => r.spec.cron, render: (r) => <span className="mono">{r.spec.cron}</span> },
    {
      key: 'next',
      header: '下次触发',
      sortValue: (r) => (nextFireTimes(r.spec.cron, 1, new Date())[0]?.getTime() ?? Number.MAX_SAFE_INTEGER),
      render: (r) => {
        const t = nextFireTimes(r.spec.cron, 1, new Date())[0];
        return <span className="num">{t ? formatDateTime(t) : '—'}</span>;
      },
    },
    { key: 'fn', header: '目标函数', sortValue: (r) => r.spec.functionref?.name ?? '', render: (r) => <span className="mono">{r.spec.functionref?.name ?? '—'}</span> },
  ];

  const formFields = (f: FormState, setF: (patch: Partial<FormState>) => void, busy: boolean, label: string, onSubmit: () => void): ReactElement => (
    <div>
      <div className="form-row">
        <div className="field">
          <span>命名空间</span>
          <input value={f.ns} onChange={(e) => setF({ ns: e.target.value })} />
        </div>
        <div className="field">
          <span>名称 *</span>
          <input value={f.name} onChange={(e) => setF({ name: e.target.value })} />
        </div>
      </div>
      <div className="field">
        <span>Cron 表达式 *（分 时 日 月 周）</span>
        <input className="mono" value={f.cron} onChange={(e) => setF({ cron: e.target.value })} placeholder="*/5 * * * *" />
      </div>
      <CronPreview cron={f.cron} />
      <div className="form-row">
        <div className="field">
          <span>目标函数 *</span>
          <input list="tt-fn-list" value={f.fnName} onChange={(e) => setF({ fnName: e.target.value })} />
          <datalist id="tt-fn-list">{fnNames.map((n) => <option key={n} value={n} />)}</datalist>
        </div>
        <div className="field">
          <span>函数命名空间</span>
          <input value={f.fnNs} onChange={(e) => setF({ fnNs: e.target.value })} />
        </div>
      </div>
      {cronErr ? <div className="banner" style={{ margin: '0 0 10px' }}>▲ {cronErr}</div> : null}
      <button type="button" className="btn pri" disabled={busy || !f.name || !f.fnName || Boolean(cronErr)} onClick={onSubmit}>
        {busy ? '提交中…' : label}
      </button>
    </div>
  );

  return (
    <main className="page">
      <div className="pane-h">
        定时触发器
        <span>{triggers.data?.length ?? 0} · 绛紫类别 · cron 前端校验 + 触发预览</span>
        <span className="actions">
          <button type="button" className="btn sm pri" onClick={() => { setForm({ ns: 'default', name: '', cron: '*/5 * * * *', fnName: '', fnNs: 'default' }); setCreateOpen(true); }}>+ 创建触发器</button>
        </span>
      </div>
      {triggers.isError ? <div className="banner">▲ 触发器列表不可达：{triggers.error instanceof Error ? triggers.error.message : ''}</div> : null}
      <div className="page-pad" style={{ paddingTop: 0 }}>
        <DataTable
          columns={columns}
          rows={triggers.data ?? []}
          rowKey={(r) => r.metadata.uid ?? r.metadata.name}
          initialSort={{ key: 'next', dir: 'asc' }}
          onRowClick={(r) => {
            setEditForm({
              ns: r.metadata.namespace,
              name: r.metadata.name,
              cron: r.spec.cron,
              fnName: r.spec.functionref?.name ?? '',
              fnNs: r.spec.functionref?.namespace ?? r.metadata.namespace,
            });
            setEditing(r);
          }}
        />
      </div>

      <Drawer open={createOpen} onClose={() => setCreateOpen(false)} title="创建定时触发器" subtitle="TIMERTRIGGER / CREATE">
        {formFields(form, set, muts.create.isPending, '创建', () => {
          muts.create.mutate(
            { ns: form.ns, body: toObject(form) },
            {
              onSuccess: () => { toast.show('创建触发器', form.name + ' 已创建'); setCreateOpen(false); },
              onError: (err) => toast.showError('创建触发器', err),
            },
          );
        })}
      </Drawer>

      <Drawer open={Boolean(editing && editForm)} onClose={() => setEditing(null)} title={'编辑 · ' + (editing?.metadata.name ?? '')} subtitle="TIMERTRIGGER / EDIT">
        {editing && editForm ? formFields(
          editForm,
          (patch) => setEditForm((f) => (f ? { ...f, ...patch } : f)),
          muts.update.isPending,
          '保存',
          () => {
            muts.update.mutate(
              { ns: editForm.ns, name: editForm.name, body: toObject(editForm) },
              {
                onSuccess: () => { toast.show('编辑触发器', editForm.name + ' 已保存'); setEditing(null); },
                onError: (err) => toast.showError('编辑触发器', err),
              },
            );
          },
        ) : null}
      </Drawer>

      <ConfirmDialog
        open={Boolean(deleting)}
        title="删除触发器"
        message={'将删除 ' + (deleting?.metadata.namespace ?? '') + '/' + (deleting?.metadata.name ?? '') + '。'}
        confirmName={deleting?.metadata.name}
        onConfirm={() => {
          if (!deleting) return;
          muts.remove.mutate(
            { ns: deleting.metadata.namespace, name: deleting.metadata.name },
            {
              onSuccess: () => { toast.show('删除触发器', deleting.metadata.name + ' 已删除'); setDeleting(null); },
              onError: (err) => { toast.showError('删除触发器', err); setDeleting(null); },
            },
          );
        }}
        onCancel={() => setDeleting(null)}
      />

      <div className="page-pad">
        <hr className="hairline" />
        <div className="pane-h" style={{ paddingLeft: 0 }}>KubernetesWatch<span>集群未安装时直接展示后端 message</span></div>
        <K8sWatchList />
      </div>
    </main>
  );
}

function K8sWatchList(): ReactElement | null {
  const kw = useQuery<unknown[]>({
    queryKey: ['list', 'kuberneteswatches', ''],
    queryFn: () => api.get<unknown[]>('/kuberneteswatches'),
    retry: false,
  });
  if (kw.isLoading) return <div className="empty">加载中…</div>;
  if (kw.isError) {
    return <div className="cap">KubernetesWatch 不可用：后端返回 — {kw.error instanceof Error ? kw.error.message : ''}</div>;
  }
  const rows = (kw.data ?? []) as Array<{ metadata: { name: string }; spec: Record<string, unknown> }>;
  if (rows.length === 0) return <div className="empty">集群当前无 KubernetesWatch</div>;
  return (
    <table className="plain">
      <thead><tr><th>名称</th><th>NS</th><th>目标函数</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.metadata.name}>
            <td className="fn-name">{r.metadata.name}</td>
            <td className="mono">{String(r.spec.namespace ?? '—')}</td>
            <td className="mono">{String((r.spec.functionref as { name?: string } | undefined)?.name ?? '—')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
