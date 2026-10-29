import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { dump as yamlDump, load as yamlLoad } from 'js-yaml';
import { useList, useResourceMutations, useDescribe, useFunctionPods, useFunctionEvents, useFunctionLogs } from '../api/hooks';
import type { Function, Environment, HTTPTrigger, TimerTrigger, FnStatus } from '../api/types';
import { DataTable, StatusBadge, LogPane, YamlEditor, ConfirmDialog, Drawer, TestConsole, useToast } from '../components';
import type { Column } from '../components';
import { isViewed, markViewed, rowId } from '../lib/readstate';

const KIND = 'functions';

/** Runtime status synthesis: explicit ready flag first, then replicas. */
function fnStatus(f: Function): FnStatus {
  const s = f.status;
  if (!s) return 'ready';
  if (s.ready === false) return 'error';
  if (typeof s.desiredReplicas === 'number' && typeof s.replicas === 'number' && s.replicas === 0 && s.desiredReplicas > 0) return 'pending';
  return 'ready';
}

const STATUS_TEXT: Record<FnStatus, string> = { ready: 'READY', error: 'ERROR', pending: 'PENDING' };

export function executorOf(f: Function): string {
  // 以 spec.InvokeStrategy.ExecutionStrategy.ExecutorType 为准（对齐 functions_ext.py），注解兜底
  const s = f.spec as unknown as { InvokeStrategy?: { ExecutionStrategy?: { ExecutorType?: string } } };
  const t = s?.InvokeStrategy?.ExecutionStrategy?.ExecutorType;
  if (t) return String(t);
  const ann = f.metadata.annotations ?? {};
  return String(ann['fission.io/executor'] ?? ann.executor ?? 'poolmgr');
}

function cpuOf(f: Function): string {
  const lim = f.spec.resources?.limits ?? {};
  return String(lim.cpu ?? '—');
}

function p99Of(f: Function): string {
  const v = f.status?.['p99LatencyMs'];
  return typeof v === 'number' ? v + 'ms' : '—';
}

/* ---------------- describe normalization ---------------- */

type Tone = 'ok' | 'pending' | 'error' | 'neutral';

interface NormalizedDescribe {
  buildLabel: string;
  buildTone: Tone;
  warmPods: number | undefined;
  invocable: string;
  mcpExposed: boolean | undefined;
  triggers: unknown[];
}

/** Map the raw /describe payload (fission CRD shapes) onto inspector fields. */
/** 触发器→函数匹配：v1.27 functionref 为对象，加权形态内嵌 functionweights。 */
export function triggerMatches(t: { spec: Record<string, unknown> }, name: string): boolean {
  const ref = t.spec?.functionref as { name?: string; functionweights?: Record<string, unknown> } | undefined;
  if (ref && typeof ref === 'object') {
    if (ref.name === name && ref.name) return true;
    if (ref.functionweights && name in ref.functionweights) return true;
    return false;
  }
  return (t.spec?.functionref as unknown) === name || Boolean((t.spec as { functionweights?: Record<string, unknown> })?.functionweights?.[name]);
}

function normalizeDescribe(d: Record<string, unknown>): NormalizedDescribe {
  const pkg = d.package as Record<string, unknown> | null | undefined;
  const rawPkgStatus = pkg?.status as Record<string, unknown> | string | undefined;
  const bs =
    (typeof rawPkgStatus === 'object' && rawPkgStatus !== null
      ? ((rawPkgStatus as Record<string, unknown>).buildstatus as Record<string, unknown> | string | undefined)
      : rawPkgStatus);
  const buildStatus =
    (typeof bs === 'object' && bs !== null ? (bs as Record<string, unknown>).status : bs)
    ?? pkg?.buildStatus
    ?? 'none';

  let buildLabel: string = String(buildStatus).toLowerCase();
  let buildTone: Tone = 'neutral';
  if (buildLabel === 'succeeded') { buildLabel = 'READY'; buildTone = 'ok'; }
  else if (buildLabel === 'running' || buildLabel === 'pending') { buildLabel = 'PENDING'; buildTone = 'pending'; }
  else if (buildLabel === 'failed') { buildLabel = 'ERROR'; buildTone = 'error'; }
  else if (buildLabel === 'none' || buildLabel === 'unknown' || buildLabel === '') { buildLabel = '未构建'; buildTone = 'neutral'; }

  const pods = d.pods as { warm?: number } | null | undefined;
  const invocable = d.invocable as string | boolean | undefined;
  const mcp = d.mcpToolExposed as { enabled?: boolean } | null | undefined;
  return {
    buildLabel,
    buildTone,
    warmPods: typeof pods?.warm === 'number' ? pods.warm : undefined,
    invocable: typeof invocable === 'string' && invocable ? invocable : invocable ? 'Yes' : 'No',
    mcpExposed: mcp ? mcp.enabled : undefined,
    triggers: (d.triggers as unknown[] | undefined) ?? [],
  };
}

/* ---------------- create / edit drawer ---------------- */

interface FnFormState {
  ns: string;
  name: string;
  env: string;
  mode: 'pkgref' | 'inline';
  pkgName: string;
  pkgNs: string;
  inlineCode: string;
  executor: 'poolmgr' | 'newdeploy';
  cpu: string;
  memory: string;
  timeout: number;
}

function emptyForm(ns: string): FnFormState {
  return { ns, name: '', env: '', mode: 'pkgref', pkgName: '', pkgNs: ns, inlineCode: '', executor: 'poolmgr', cpu: '100m', memory: '128Mi', timeout: 60 };
}

function functionFromForm(f: FnFormState, pkgRefName: string): Function {
  return {
    apiVersion: 'fission.io/v1',
    kind: 'Function',
    metadata: { name: f.name, namespace: f.ns },
    spec: {
      environment: { name: f.env, namespace: f.ns },
      package: { packageref: { name: pkgRefName, namespace: f.pkgNs || f.ns } },
      resources: { limits: { cpu: f.cpu, memory: f.memory } },
      functionTimeout: f.timeout,
    },
  };
}

function CreateDrawer({ open, onClose, namespaces, envs }: { open: boolean; onClose: () => void; namespaces: string[]; envs: Environment[] }): ReactElement | null {
  const toast = useToast();
  const muts = useResourceMutations<Function>(KIND);
  const pkgMuts = useResourceMutations('packages');
  const [form, setForm] = useState<FnFormState>(() => emptyForm(namespaces[0] ?? 'default'));
  const set = (patch: Partial<FnFormState>): void => setForm((f) => ({ ...f, ...patch }));

  useEffect(() => {
    if (open) setForm(emptyForm(namespaces[0] ?? 'default'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = (): void => {
    if (!form.name || !form.env) {
      toast.show('创建函数', '名称与环境为必填项', 'error');
      return;
    }
    const createFn = (pkgRefName: string): void => {
      muts.create.mutate(
        { ns: form.ns, body: functionFromForm(form, pkgRefName) },
        {
          onSuccess: () => { toast.show('创建函数', form.name + ' 已创建'); onClose(); },
          onError: (err) => toast.showError('创建函数', err),
        },
      );
    };
    if (form.mode === 'inline') {
      const pkgName = form.name + '-pkg';
      pkgMuts.create.mutate(
        {
          ns: form.ns,
          body: {
            apiVersion: 'fission.io/v1',
            kind: 'Package',
            metadata: { name: pkgName, namespace: form.ns },
            spec: {
              environment: { name: form.env, namespace: form.ns },
              source: { type: 'literal', literal: { 'code.py': form.inlineCode } },
              deployment: { type: 'literal', literal: { 'code.py': form.inlineCode } },
            },
          } as unknown as import('../api/types').Package,
        },
        {
          onSuccess: () => { toast.show('创建包', pkgName + ' 已创建，开始构建'); createFn(pkgName); },
          onError: (err) => toast.showError('创建包', err),
        },
      );
    } else {
      if (!form.pkgName) {
        toast.show('创建函数', '请填写包引用名称', 'error');
        return;
      }
      createFn(form.pkgName);
    }
  };

  return (
    <Drawer open={open} onClose={onClose} title="创建函数" subtitle="FUNCTION / CREATE">
      <div className="form-row">
        <div className="field">
          <span>命名空间</span>
          <select value={form.ns} onChange={(e) => set({ ns: e.target.value })}>
            {(namespaces.length ? namespaces : ['default']).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <div className="field">
          <span>名称 *</span>
          <input value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="my-fn" />
        </div>
      </div>
      <div className="field">
        <span>环境 *</span>
        <select value={form.env} onChange={(e) => set({ env: e.target.value })}>
          <option value="" disabled>选择环境…</option>
          {envs.map((e) => <option key={e.metadata.name} value={e.metadata.name}>{e.metadata.name} (v{e.spec.version})</option>)}
        </select>
      </div>
      <div className="field">
        <span>包来源</span>
        <select value={form.mode} onChange={(e) => set({ mode: e.target.value as FnFormState['mode'] })}>
          <option value="pkgref">引用现有包</option>
          <option value="inline">内联代码（自动建包）</option>
        </select>
      </div>
      {form.mode === 'pkgref' ? (
        <div className="form-row">
          <div className="field">
            <span>包名</span>
            <input value={form.pkgName} onChange={(e) => set({ pkgName: e.target.value })} placeholder="my-fn-pkg" />
          </div>
          <div className="field">
            <span>包命名空间</span>
            <input value={form.pkgNs} onChange={(e) => set({ pkgNs: e.target.value })} />
          </div>
        </div>
      ) : (
        <div className="field">
          <span>函数代码</span>
          <textarea rows={6} className="mono" style={{ fontSize: 12 }} value={form.inlineCode} onChange={(e) => set({ inlineCode: e.target.value })} placeholder={'def main():\n    return "hello"}'} />
        </div>
      )}
      <div className="form-row">
        <div className="field">
          <span>执行策略 InvokeStrategy</span>
          <select value={form.executor} onChange={(e) => set({ executor: e.target.value as FnFormState['executor'] })}>
            <option value="poolmgr">poolmgr（热池）</option>
            <option value="newdeploy">newdeploy（专用部署）</option>
          </select>
        </div>
        <div className="field">
          <span>超时（秒，默认 60）</span>
          <input type="number" min={1} value={form.timeout} onChange={(e) => set({ timeout: Number(e.target.value) || 60 })} />
        </div>
      </div>
      <div className="form-row">
        <div className="field">
          <span>CPU limits</span>
          <input value={form.cpu} onChange={(e) => set({ cpu: e.target.value })} placeholder="100m" />
        </div>
        <div className="field">
          <span>内存 limits</span>
          <input value={form.memory} onChange={(e) => set({ memory: e.target.value })} placeholder="128Mi" />
        </div>
      </div>
      <div className="cap">内联代码将先创建 Package 并触发构建；poolmgr 依赖环境池尺寸，newdeploy 为每函数专用 Deployment。</div>
      <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
        <button type="button" className="btn pri" disabled={muts.create.isPending || pkgMuts.create.isPending} onClick={submit}>
          {muts.create.isPending || pkgMuts.create.isPending ? '创建中…' : '创建'}
        </button>
        <button type="button" className="btn" onClick={onClose}>取消</button>
      </div>
    </Drawer>
  );
}

/* ---------------- inspector ---------------- */

type Tab = 'logs' | 'test' | 'yaml' | 'events' | 'triggers';

function downloadText(name: string, text: string): void {
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function Inspector({ fn, onDeleted }: { fn: Function; onDeleted: () => void }): ReactElement {
  const toast = useToast();
  const muts = useResourceMutations<Function>(KIND);
  const [tab, setTab] = useState<Tab>('logs');
  const [pod, setPod] = useState<string>('');
  const [follow, setFollow] = useState(true);
  const [requestId, setRequestId] = useState<string>('');
  const [yamlText, setYamlText] = useState<string>('');
  const [yamlDirty, setYamlDirty] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const lastLoadedRef = useRef<string>('');

  const ns = fn.metadata.namespace;
  const name = fn.metadata.name;
  const describe = useDescribe(ns, name);
  const pods = useFunctionPods(ns, name);
  const events = useFunctionEvents(ns, name);
  const httpT = useList<HTTPTrigger>('httptriggers');
  const timerT = useList<TimerTrigger>('timetriggers');
  const logs = useFunctionLogs(ns, name, pod || undefined, follow);

  // load YAML from the live object
  useEffect(() => {
    const key = fn.metadata.resourceVersion ?? '';
    if (!yamlDirty && lastLoadedRef.current !== key) {
      lastLoadedRef.current = key;
      setYamlText(yamlDump(fn, { lineWidth: -1 }));
    }
  }, [fn, yamlDirty]);

  const fnsTriggers = useMemo(() => {
    const http = (httpT.data ?? []).filter((t) => triggerMatches(t as never, name)).map((t) => ({ type: 'HTTP', name: t.metadata.name, detail: (t.spec.method ?? 'GET') + ' ' + (t.spec.host ? t.spec.host + '/' : '') + (t.spec.relativeurl ?? '') }));
    const timer = (timerT.data ?? []).filter((t) => triggerMatches(t as never, name)).map((t) => ({ type: 'TIMER', name: t.metadata.name, detail: 'cron ' + t.spec.cron }));
    return [...http, ...timer];
  }, [httpT.data, timerT.data, name]);

  const saveYaml = (): void => {
    try {
      const parsed = yamlLoad(yamlText) as Function;
      if (!parsed?.metadata?.name) throw new Error('YAML 缺少 metadata.name');
      muts.update.mutate(
        { ns: parsed.metadata.namespace ?? ns, name: parsed.metadata.name, body: parsed },
        {
          onSuccess: () => { toast.show('YAML', '已保存'); setYamlDirty(false); },
          onError: (err) => toast.showError('YAML 保存', err),
        },
      );
    } catch (e) {
      toast.showError('YAML 解析', e);
    }
  };

  const remove = (): void => {
    muts.remove.mutate(
      { ns, name },
      {
        onSuccess: () => { toast.show('删除函数', name + ' 已删除'); setDelOpen(false); onDeleted(); },
        onError: (err) => { toast.showError('删除函数', err); setDelOpen(false); },
      },
    );
  };

  const status = fnStatus(fn);
  const d = describe.data ? normalizeDescribe(describe.data as unknown as Record<string, unknown>) : null;

  return (
    <section className="inspector">
      <div className="ins-h">
        <h2>{name}</h2>
        <StatusBadge status={STATUS_TEXT[status]} qualifier={status === 'pending' ? '特化中' : undefined} />
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button type="button" className="btn sm" onClick={() => setEditOpen(true)}>编辑</button>
          <button type="button" className="btn sm danger" onClick={() => setDelOpen(true)}>删除</button>
          <button type="button" className="btn sm pri" onClick={() => setTab('test')}>发起调用</button>
        </div>
      </div>
      <p className="meta">
        环境 <b>{fn.spec.environment?.name}</b> · {executorOf(fn)}　包引用 <b className="mono">{fn.spec.package?.packageref?.name ?? '—'}</b>
        {fn.spec.functionTimeout ? <>　超时 <b className="mono">{fn.spec.functionTimeout}s</b></> : null}
      </p>

      <div className="obs-card">
        <h3>DESCRIBE · 构建与运行时</h3>
        {describe.unavailable ? (
          <div className="cap">describe 端点尚未上线（501）——以下为列表数据降级占位。</div>
        ) : d ? (
          <table className="plain">
            <tbody>
              <tr><th>构建状态</th><td><StatusBadge status={d.buildLabel} tone={d.buildTone} /></td></tr>
              <tr><th>warm pods</th><td className="num">{d.warmPods ?? '—'}</td></tr>
              <tr>
                <th>invocable</th>
                <td className="mono">
                  {d.invocable}
                  {d.invocable.includes('cold start') ? <span className="muted">　首次调用冷启动</span> : null}
                </td>
              </tr>
              <tr><th>MCP 暴露</th><td className="mono">{d.mcpExposed === undefined ? '—' : String(d.mcpExposed)}</td></tr>
            </tbody>
          </table>
        ) : (
          <div className="cap">{describe.data === null ? '加载中…' : '暂无 describe 数据'}</div>
        )}
      </div>

      <div className="tabs" role="tablist">
        {([['logs', '日志'], ['test', '调用测试'], ['yaml', 'YAML'], ['events', '事件'], ['triggers', '触发器 ' + fnsTriggers.length]] as const).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? 'on' : ''} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>

      {tab === 'logs' ? (
        <div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
            <div className="field" style={{ marginBottom: 0, minWidth: 200 }}>
              <span>Pod</span>
              <select value={pod} onChange={(e) => setPod(e.target.value)}>
                <option value="">全部</option>
                {(pods.data ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
              </select>
            </div>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, marginTop: 14 }}>
              <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow 实时
            </label>
            <button type="button" className="btn sm" style={{ marginTop: 12 }} onClick={() => downloadText(name + '.log', logs.lines.join('\n'))}>下载</button>
            {requestId ? (
              <span className="cap" style={{ marginTop: 14 }}>
                requestId 过滤: <span className="mono">{requestId}</span>
                <button type="button" className="btn sm" style={{ marginLeft: 6 }} onClick={() => setRequestId('')}>清除</button>
              </span>
            ) : null}
          </div>
          {logs.error ? <div className="banner" style={{ margin: '0 0 8px' }}>▲ {logs.error}</div> : null}
          <LogPane
            lines={requestId ? logs.lines.filter((l) => l.includes(requestId)) : logs.lines}
            connected={follow && logs.connected}
          />
        </div>
      ) : null}

      {tab === 'test' ? (
        <TestConsole namespace={ns} name={name} onJumpLogs={(rid) => { setRequestId(rid); setFollow(false); setTab('logs'); }} />
      ) : null}

      {tab === 'yaml' ? (
        <div>
          <YamlEditor value={yamlText} onChange={(v) => { setYamlText(v); setYamlDirty(true); }} height="380px" />
          <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
            <button type="button" className="btn pri" disabled={!yamlDirty || muts.update.isPending} onClick={saveYaml}>保存 YAML</button>
            <button type="button" className="btn" onClick={() => { setYamlDirty(false); setYamlText(yamlDump(fn, { lineWidth: -1 })); }}>还原</button>
            {yamlDirty ? <span className="cap" style={{ marginTop: 8 }}>有未保存修改</span> : null}
          </div>
        </div>
      ) : null}

      {tab === 'events' ? (
        events.unavailable ? (
          <div className="cap">events 端点尚未上线（501）——降级占位。</div>
        ) : (events.data ?? []).length === 0 ? (
          <div className="empty">暂无事件</div>
        ) : (
          <table className="plain">
            <thead><tr><th>时间</th><th>类型</th><th>消息</th></tr></thead>
            <tbody>
              {(events.data ?? []).map((e, i) => (
                <tr key={i}>
                  <td className="num">{(e as { lastTimestamp?: string }).lastTimestamp ?? e.timestamp ?? '—'}</td>
                  <td className="mono">{e.type ?? e.reason ?? '—'}</td>
                  <td>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}

      {tab === 'triggers' ? (
        fnsTriggers.length === 0 ? (
          <div className="empty">该函数暂无触发器</div>
        ) : (
          <table className="plain">
            <thead><tr><th>类型</th><th>名称</th><th>详情</th></tr></thead>
            <tbody>
              {fnsTriggers.map((t) => (
                <tr key={t.type + t.name}>
                  <td className="mono">{t.type}</td>
                  <td className="fn-name">{t.name}</td>
                  <td className="mono">{t.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}

      <Drawer
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title={'编辑 · ' + name}
        subtitle="FUNCTION / EDIT"
        footer={
          <>
            <button type="button" className="btn pri" onClick={() => { setEditOpen(false); setTab('yaml'); }}>以 YAML 编辑</button>
            <button type="button" className="btn" onClick={() => setEditOpen(false)}>关闭</button>
          </>
        }
      >
        <div className="cap" style={{ marginBottom: 10 }}>表单模式改动会合并进 YAML 并整体 PUT 提交。</div>
        <div className="form-row">
          <div className="field">
            <span>执行策略</span>
            <select value={executorOf(fn)} onChange={(e) => {
              const next = { ...fn, metadata: { ...fn.metadata }, spec: { ...fn.spec } } as Function;
              const spec = next.spec as unknown as { InvokeStrategy?: { ExecutionStrategy?: Record<string, unknown> } };
              spec.InvokeStrategy = { ...(spec.InvokeStrategy ?? {}), ExecutionStrategy: { ...(spec.InvokeStrategy?.ExecutionStrategy ?? {}), ExecutorType: e.target.value } };
              muts.update.mutate({ ns, name, body: next }, { onSuccess: () => toast.show('编辑', '执行策略已更新'), onError: (err) => toast.showError('编辑', err) });
            }}>
              <option value="poolmgr">poolmgr</option>
              <option value="newdeploy">newdeploy</option>
            </select>
          </div>
          <div className="field">
            <span>超时（秒）</span>
            <input type="number" min={1} defaultValue={fn.spec.functionTimeout ?? 60} onBlur={(e) => {
              const v = Number(e.target.value) || 60;
              if (v === (fn.spec.functionTimeout ?? 60)) return;
              const next = { ...fn, spec: { ...fn.spec, functionTimeout: v } } as Function;
              muts.update.mutate({ ns, name, body: next }, { onSuccess: () => toast.show('编辑', '超时已更新'), onError: (err) => toast.showError('编辑', err) });
            }} />
          </div>
        </div>
        <div className="form-row">
          <div className="field">
            <span>CPU limits</span>
            <input defaultValue={fn.spec.resources?.limits?.cpu ?? ''} onBlur={(e) => {
              if (e.target.value === (fn.spec.resources?.limits?.cpu ?? '')) return;
              const next = { ...fn, spec: { ...fn.spec, resources: { limits: { ...(fn.spec.resources?.limits ?? {}), cpu: e.target.value } } } } as Function;
              muts.update.mutate({ ns, name, body: next }, { onSuccess: () => toast.show('编辑', 'CPU 已更新'), onError: (err) => toast.showError('编辑', err) });
            }} />
          </div>
          <div className="field">
            <span>内存 limits</span>
            <input defaultValue={fn.spec.resources?.limits?.memory ?? ''} onBlur={(e) => {
              if (e.target.value === (fn.spec.resources?.limits?.memory ?? '')) return;
              const next = { ...fn, spec: { ...fn.spec, resources: { limits: { ...(fn.spec.resources?.limits ?? {}), memory: e.target.value } } } } as Function;
              muts.update.mutate({ ns, name, body: next }, { onSuccess: () => toast.show('编辑', '内存已更新'), onError: (err) => toast.showError('编辑', err) });
            }} />
          </div>
        </div>
      </Drawer>

      <ConfirmDialog
        open={delOpen}
        title="删除函数"
        message={'将删除 ' + ns + '/' + name + '。此操作不可撤销。'}
        confirmName={name}
        onConfirm={remove}
        onCancel={() => setDelOpen(false)}
      />
    </section>
  );
}

/* ---------------- page ---------------- */

export default function Functions(): ReactElement {
  const params = useParams<{ ns?: string; name?: string }>();
  const navigate = useNavigate();
  const functions = useList<Function>(KIND);
  const envs = useList<Environment>('environments');
  const [selected, setSelected] = useState<{ ns: string; name: string } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    if (params.ns && params.name) setSelected({ ns: params.ns, name: params.name });
  }, [params.ns, params.name]);

  const namespaces = useMemo(() => {
    const s = new Set<string>((functions.data ?? []).map((f) => f.metadata.namespace));
    if (s.size === 0) s.add('default');
    return Array.from(s).sort();
  }, [functions.data]);

  const sorted = useMemo(() => {
    const list = [...(functions.data ?? [])];
    list.sort((a, b) => {
      const ea = fnStatus(a) === 'error' ? 0 : 1;
      const eb = fnStatus(b) === 'error' ? 0 : 1;
      if (ea !== eb) return ea - eb; // 异常置顶
      return a.metadata.name.localeCompare(b.metadata.name);
    });
    return list;
  }, [functions.data]);

  const columns: Column<Function>[] = [
    {
      key: 'name',
      header: '名称',
      sortValue: (r) => r.metadata.name,
      render: (r) => (
        <div>
          <div className="fn-name" title={r.metadata.name}>{r.metadata.name}</div>
          <div className="fn-sub mono">{r.spec.environment?.name} · {executorOf(r)}{cpuOf(r) !== '—' ? <> · CPU {cpuOf(r)}</> : null}</div>
        </div>
      ),
    },
    { key: 'env', header: '环境', sortValue: (r) => r.spec.environment?.name ?? '', render: (r) => <span className="mono">{r.spec.environment?.name ?? '—'}</span> },
    { key: 'executor', header: '执行器', sortValue: (r) => executorOf(r), render: (r) => <span className="mono">{executorOf(r)}</span> },
    {
      key: 'replicas',
      header: '副本',
      sortValue: (r) => r.status?.replicas ?? 0,
      render: (r) => <span className="num">{r.status?.replicas ?? '—'}/{r.status?.desiredReplicas ?? '—'}</span>,
    },
    { key: 'cpu', header: 'CPU', sortValue: (r) => cpuOf(r), render: (r) => <span className="num">{cpuOf(r)}</span> },
    { key: 'p99', header: 'P99', sortValue: (r) => p99Of(r), render: (r) => <span className="num">{p99Of(r)}</span> },
    {
      key: 'status',
      header: '状态',
      sortValue: (r) => fnStatus(r),
      render: (r) => <StatusBadge status={STATUS_TEXT[fnStatus(r)]} qualifier={fnStatus(r) === 'pending' ? '特化中' : undefined} />,
    },
  ];

  const selectedFn = useMemo(
    () => (functions.data ?? []).find((f) => selected && f.metadata.namespace === selected.ns && f.metadata.name === selected.name) ?? null,
    [functions.data, selected],
  );

  const select = (f: Function): void => {
    markViewed(rowId(KIND, f.metadata.namespace, f.metadata.name));
    setSelected({ ns: f.metadata.namespace, name: f.metadata.name });
    navigate('/functions/' + encodeURIComponent(f.metadata.namespace) + '/' + encodeURIComponent(f.metadata.name));
  };

  return (
    <div className="shell" style={{ flex: 1, minHeight: 0 }}>
      <div className="list-pane">
        <div className="pane-h">
          函数观测表
          <span>{sorted.length} · 异常置顶 · 发丝线紧排</span>
          <span className="actions">
            <button type="button" className="btn sm pri" onClick={() => setCreateOpen(true)}>+ 创建函数</button>
          </span>
        </div>
        <div className="list-scroll">
          {functions.isError ? (
            <div className="banner">▲ 函数列表不可达：{functions.error instanceof Error ? functions.error.message : ''}</div>
          ) : functions.isLoading ? (
            <div className="empty">加载中…</div>
          ) : (
            <DataTable
              columns={columns}
              rows={sorted}
              rowKey={(r) => rowId(KIND, r.metadata.namespace, r.metadata.name)}
              onRowClick={select}
              initialSort={{ key: 'name', dir: 'asc' }}
              rowClass={(r) => {
                const err = fnStatus(r) === 'error';
                const unread = !isViewed(rowId(KIND, r.metadata.namespace, r.metadata.name));
                const sel = selected?.ns === r.metadata.namespace && selected?.name === r.metadata.name;
                return (err && unread ? 'row-alert ' : '') + (sel ? 'row-sel' : '');
              }}
            />
          )}
        </div>
      </div>
      {selectedFn ? (
        <Inspector fn={selectedFn} onDeleted={() => setSelected(null)} />
      ) : (
        <section className="inspector">
          <div className="empty" style={{ marginTop: 64 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 18, letterSpacing: 1, marginBottom: 8 }}>选择一条观测记录</div>
            <p>从左侧观测表选择函数，查看 describe、日志、调用测试、YAML 与触发器。</p>
            <p className="cap">Ctrl+K 打开命令面板 · 可直接搜索函数名</p>
          </div>
        </section>
      )}
      <CreateDrawer open={createOpen} onClose={() => setCreateOpen(false)} namespaces={namespaces} envs={envs.data ?? []} />
    </div>
  );
}

export { normalizeDescribe };
