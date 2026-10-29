import { useEffect, useState, type ReactElement } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { useList, useResourceMutations, useWatch } from '../api/hooks';
import type { HTTPTrigger } from '../api/types';
import { DataTable, KindDot, Drawer, ConfirmDialog, useToast } from '../components';
import type { Column } from '../components';

const KIND = 'httptriggers';

interface FormState {
  ns: string; name: string; host: string; relativeurl: string;
  method: string; createingress: boolean; fnName: string; fnNs: string;
}

function empty(ns: string): FormState {
  return { ns, name: '', host: '', relativeurl: '/', method: 'GET', createingress: false, fnName: '', fnNs: ns };
}

function toObject(f: FormState): HTTPTrigger {
  return {
    apiVersion: 'fission.io/v1',
    kind: 'HTTPTrigger',
    metadata: { name: f.name, namespace: f.ns },
    spec: {
      relativeurl: f.relativeurl,
      host: f.host || undefined,
      method: f.method,
      createingress: f.createingress,
      functionref: { name: f.fnName, namespace: f.fnNs || f.ns },
    },
  };
}

function TriggerForm({ initial, functions, onSubmit, busy, submitLabel }: {
  initial: FormState;
  functions: string[];
  onSubmit: (f: FormState) => void;
  busy: boolean;
  submitLabel: string;
}): ReactElement {
  const [f, setF] = useState<FormState>(initial);
  const set = (patch: Partial<FormState>): void => setF((old) => ({ ...old, ...patch }));
  const urlErr = !f.relativeurl.startsWith('/') && !f.host ? 'host 或以 / 开头的相对路径至少一项' : '';
  return (
    <div>
      <div className="form-row">
        <div className="field">
          <span>命名空间</span>
          <input value={f.ns} onChange={(e) => set({ ns: e.target.value })} />
        </div>
        <div className="field">
          <span>名称 *</span>
          <input value={f.name} onChange={(e) => set({ name: e.target.value })} />
        </div>
      </div>
      <div className="form-row">
        <div className="field">
          <span>Host（可选）</span>
          <input value={f.host} onChange={(e) => set({ host: e.target.value })} placeholder="api.example.com" />
        </div>
        <div className="field">
          <span>相对路径 *</span>
          <input value={f.relativeurl} onChange={(e) => set({ relativeurl: e.target.value })} placeholder="/hello" />
        </div>
      </div>
      <div className="form-row">
        <div className="field">
          <span>方法</span>
          <select value={f.method} onChange={(e) => set({ method: e.target.value })}>
            {['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD'].map((m) => <option key={m}>{m}</option>)}
          </select>
        </div>
        <div className="field">
          <span>创建 Ingress</span>
          <select value={f.createingress ? 'yes' : 'no'} onChange={(e) => set({ createingress: e.target.value === 'yes' })}>
            <option value="no">否</option>
            <option value="yes">是</option>
          </select>
        </div>
      </div>
      <div className="form-row">
        <div className="field">
          <span>目标函数 *</span>
          <input list="fn-list" value={f.fnName} onChange={(e) => set({ fnName: e.target.value })} />
          <datalist id="fn-list">
            {functions.map((n) => <option key={n} value={n} />)}
          </datalist>
        </div>
        <div className="field">
          <span>函数命名空间</span>
          <input value={f.fnNs} onChange={(e) => set({ fnNs: e.target.value })} />
        </div>
      </div>
      {urlErr ? <div className="banner" style={{ margin: '0 0 10px' }}>▲ {urlErr}</div> : null}
      <button type="button" className="btn pri" disabled={busy || !f.name || !f.fnName} onClick={() => onSubmit(f)}>
        {busy ? '提交中…' : submitLabel}
      </button>
    </div>
  );
}

export default function HTTPTriggers(): ReactElement {
  const triggers = useList<HTTPTrigger>(KIND);
  useWatch(KIND);
  const muts = useResourceMutations<HTTPTrigger>(KIND);
  const functions = useList('functions');
  const toast = useToast();

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<HTTPTrigger | null>(null);
  const [deleting, setDeleting] = useState<HTTPTrigger | null>(null);

  // optional MQ triggers — degrade to backend message when unsupported
  const mq = useQuery<unknown[]>({
    queryKey: ['list', 'mqtriggers', ''],
    queryFn: () => api.get<unknown[]>('/mqtriggers'),
    retry: false,
  });

  const fnNames = (functions.data ?? []).map((f) => f.metadata.name);

  useEffect(() => {
    if (mq.error) {
      // backend without messagequeue support returns 404 — surface, not fatal
      console.info('mqtriggers unavailable:', mq.error.message);
    }
  }, [mq.error]);

  const columns: Column<HTTPTrigger>[] = [
    {
      key: 'name',
      header: '名称',
      sortValue: (r) => r.metadata.name,
      render: (r) => (
        <div>
          <div className="fn-name" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <KindDot kind="httptriggers" />{r.metadata.name}
          </div>
          <div className="fn-sub mono">{r.metadata.namespace}</div>
        </div>
      ),
    },
    {
      key: 'url',
      header: '路由',
      sortValue: (r) => (r.spec.host ?? '') + r.spec.relativeurl,
      render: (r) => <span className="mono">{(r.spec.method ?? 'GET') + ' ' + (r.spec.host ? r.spec.host : '') + r.spec.relativeurl}</span>,
    },
    { key: 'fn', header: '目标函数', sortValue: (r) => r.spec.functionref?.name ?? '', render: (r) => <span className="mono">{r.spec.functionref?.name ?? '—'}</span> },
    { key: 'ingress', header: 'Ingress', sortValue: (r) => (r.spec.createingress ? 1 : 0), render: (r) => <span className="mono">{r.spec.createingress ? 'yes' : 'no'}</span> },
  ];

  return (
    <main className="page">
      <div className="pane-h">
        HTTP 触发器
        <span>{triggers.data?.length ?? 0} · 群青类别</span>
        <span className="actions">
          <button type="button" className="btn sm pri" onClick={() => setCreateOpen(true)}>+ 创建触发器</button>
        </span>
      </div>
      {triggers.isError ? <div className="banner">▲ 触发器列表不可达：{triggers.error instanceof Error ? triggers.error.message : ''}</div> : null}
      <div className="page-pad" style={{ paddingTop: 0 }}>
        <DataTable
          columns={columns}
          rows={triggers.data ?? []}
          rowKey={(r) => r.metadata.uid ?? r.metadata.name}
          initialSort={{ key: 'name', dir: 'asc' }}
          onRowClick={setEditing}
        />
      </div>

      <Drawer open={createOpen} onClose={() => setCreateOpen(false)} title="创建 HTTP 触发器" subtitle="HTTPTRIGGER / CREATE">
        <TriggerForm
          initial={empty('default')}
          functions={fnNames}
          busy={muts.create.isPending}
          submitLabel="创建"
          onSubmit={(f) => muts.create.mutate(
            { ns: f.ns, body: toObject(f) },
            {
              onSuccess: () => { toast.show('创建触发器', f.name + ' 已创建'); setCreateOpen(false); },
              onError: (err) => toast.showError('创建触发器', err),
            },
          )}
        />
      </Drawer>

      <Drawer open={Boolean(editing)} onClose={() => setEditing(null)} title={'编辑 · ' + (editing?.metadata.name ?? '')} subtitle="HTTPTRIGGER / EDIT">
        {editing ? (
          <TriggerForm
            initial={{
              ns: editing.metadata.namespace,
              name: editing.metadata.name,
              host: editing.spec.host ?? '',
              relativeurl: editing.spec.relativeurl,
              method: editing.spec.method ?? 'GET',
              createingress: Boolean(editing.spec.createingress),
              fnName: editing.spec.functionref?.name ?? '',
              fnNs: editing.spec.functionref?.namespace ?? editing.metadata.namespace,
            }}
            functions={fnNames}
            busy={muts.update.isPending}
            submitLabel="保存"
            onSubmit={(f) => muts.update.mutate(
              { ns: f.ns, name: f.name, body: toObject(f) },
              {
                onSuccess: () => { toast.show('编辑触发器', f.name + ' 已保存'); setEditing(null); },
                onError: (err) => toast.showError('编辑触发器', err),
              },
            )}
          />
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
        <div className="pane-h" style={{ paddingLeft: 0 }}>MQ 触发器<span>集群未安装 MessageQueue 时展示后端 message</span></div>
        {mq.isLoading ? (
          <div className="empty">加载中…</div>
        ) : mq.isError ? (
          <div className="cap">MQ 触发器不可用：{mq.error instanceof Error ? mq.error.message : ''}</div>
        ) : (
          <table className="plain">
            <thead><tr><th>名称</th><th>主题</th><th>响应类型</th><th>目标函数</th></tr></thead>
            <tbody>
              {((mq.data ?? []) as Array<{ metadata: { name: string; namespace: string }; spec: Record<string, unknown> }>).map((t) => (
                <tr key={t.metadata.name}>
                  <td className="fn-name">{t.metadata.name}</td>
                  <td className="mono">{String(t.spec.topic ?? '—')}</td>
                  <td className="mono">{String(t.spec.responsetype ?? '—')}</td>
                  <td className="mono">{String((t.spec.functionref as { name?: string } | undefined)?.name ?? '—')}</td>
                </tr>
              ))}
              {(mq.data ?? []).length === 0 ? <tr><td colSpan={4} className="empty">集群当前无 MQ 触发器</td></tr> : null}
            </tbody>
          </table>
        )}
        {mq.isError && mq.error instanceof Error ? (
          <div className="banner" style={{ marginTop: 8 }}>▲ 后端返回：{mq.error.message}</div>
        ) : null}
      </div>
    </main>
  );
}
