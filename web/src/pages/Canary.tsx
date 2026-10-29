import { useMemo, useState, type ReactElement } from 'react';
import { useList } from '../api/hooks';
import {
  useCanaryConfigs,
  useCanaryCreate,
  useCanaryDelete,
  useCanaryStatus,
  useFunctionVersionList,
  useSetTriggerWeights,
} from '../api/extend';
import type { CanaryConfig, CanaryStatus, VersionInfo } from '../api/extend';
import type { HTTPTrigger } from '../api/types';
import { ConfirmDialog, Drawer, StatusBadge, useToast } from '../components';
import { getErrMessage, errStatus } from '../lib/err';

type H = HTTPTrigger & { kind: 'HTTPTrigger' };

/** Canary phase → redundant status coding (color + shape + text). */
function phaseTone(st?: CanaryStatus): { status: string; tone: 'ok' | 'pending' | 'error' | 'neutral' } {
  // 阶段字符串取 BE canary.py 归一后的 stage 键
  const raw = String(st?.stage ?? '');
  const s = raw.toLowerCase();
  if (!s) return { status: '未知', tone: 'neutral' };
  if (s === 'succeed' || s === 'succeeded') return { status: raw, tone: 'ok' };
  if (s.includes('success') || s.includes('finish') || s.includes('promot') || s.includes('complete')) return { status: raw, tone: 'ok' };
  if (s.includes('fail') || s.includes('error') || s.includes('abort')) return { status: raw, tone: 'error' };
  if (s.includes('progress') || s.includes('run') || s.includes('increment') || s.includes('wait')) return { status: raw, tone: 'pending' };
  return { status: raw, tone: 'neutral' };
}

/* ---------------- weight bar ---------------- */

function WeightBar({ weights, oldName, newName }: { weights: Record<string, number>; oldName: string; newName: string }): ReactElement {
  const oldW = Math.max(0, Math.min(100, weights[oldName] ?? 0));
  const newW = Math.max(0, Math.min(100, weights[newName] ?? 0));
  return (
    <div>
      <div style={{ display: 'flex', height: 22, border: '1px solid var(--hairline-strong)', background: 'var(--paper-raise)' }} role="img" aria-label={'权重 ' + oldW + '/' + newW}>
        <div style={{ width: oldW + '%', background: 'var(--obs)', transition: 'width .4s' }} />
        <div style={{ width: newW + '%', background: 'var(--kind-canary)', transition: 'width .4s' }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--obs)' }}>■ {oldName} · {oldW}%</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--kind-canary)' }}>{newName} · {newW}% ■</span>
      </div>
    </div>
  );
}

function CanaryDetail({ cfg, onDeleted }: { cfg: CanaryConfig; onDeleted: () => void }): ReactElement {
  const toast = useToast();
  const ns = cfg.metadata?.namespace ?? 'default';
  const name = cfg.metadata?.name ?? '';
  const spec = cfg.spec ?? {};
  const status = useCanaryStatus(ns, name);
  const weights = useSetTriggerWeights(ns, spec.trigger ?? name);
  const [delOpen, setDelOpen] = useState(false);
  const del = useCanaryDelete();

  const oldName = spec.oldfunction ?? 'old';
  const newName = spec.newfunction ?? 'new';
  const w = status.data?.functionWeights ?? {};
  const tone = phaseTone(status.data);

  const intervene = (target: string): void => {
    // 由当前 functionWeights 补齐 Σ=100：目标 100%，其余全 0
    const body: Record<string, number> = {};
    for (const k of Object.keys(w)) body[k] = 0;
    body[target] = 100;
    weights.mutate(
      { weights: body },
      {
        onSuccess: () => toast.show('权重干预', target + ' → 100%，已写入 ' + (spec.trigger ?? name)),
        onError: (err) => toast.showError('权重干预', getErrMessage(err)),
      },
    );
  };

  const thresholdHit = typeof spec.failurethreshold === 'number' && typeof status.data?.failures === 'number' && status.data.failures >= spec.failurethreshold;

  return (
    <section className="inspector">
      <div className="ins-h">
        <h2>{name}</h2>
        <StatusBadge status={tone.status} tone={tone.tone} qualifier={thresholdHit ? '达失败阈值' : undefined} />
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button type="button" className="btn sm" disabled={weights.isPending} onClick={() => intervene(oldName)}>回滚 · 旧版 100%</button>
          <button type="button" className="btn sm pri" disabled={weights.isPending} onClick={() => intervene(newName)}>晋级 · 新版 100%</button>
          <button type="button" className="btn sm danger" onClick={() => setDelOpen(true)}>删除</button>
        </div>
      </div>
      <p className="meta">
        触发器 <b className="mono">{spec.trigger ?? '—'}</b>　旧版 <b className="mono">{oldName}</b>　新版 <b className="mono">{newName}</b>
        　步进 <b className="num">{spec.weightincrement ?? '—'}%</b> / <b className="num">{spec.duration ?? '—'}</b>
        　失败阈值 <b className="num">{spec.failurethreshold ?? '—'}</b> · {String(spec.failureType ?? spec.failuretype ?? '—')}
      </p>

      {thresholdHit ? (
        <div className="banner" style={{ margin: '0 0 12px' }}>▲ 失败计数 {status.data?.failures} 已达阈值 {spec.failurethreshold} —— 建议回滚或排查后重建。</div>
      ) : null}
      {status.error && errStatus(status.error) !== 501 && errStatus(status.error) !== 404 ? (
        <div className="banner" style={{ margin: '0 0 12px' }}>▲ 状态轮询失败：{getErrMessage(status.error)}（5s 自动重试）</div>
      ) : null}

      <div className="obs-card">
        <h3>实时权重 · 5s 轮询</h3>
        <WeightBar weights={w} oldName={oldName} newName={newName} />
        <div className="cap" style={{ marginTop: 8 }}>failures <span className="num">{status.data?.failures ?? '—'}</span> · 权重写入直接 PUT /httptriggers/{spec.trigger ?? name}/weights</div>
      </div>
      <div className="cap">观测蓝 = 稳定版 · 琥珀 = 金丝雀版。手动干预会绕过步进策略直接设定权重。</div>

      <ConfirmDialog
        open={delOpen}
        title="删除金丝雀策略"
        message={'将删除 ' + ns + '/' + name + '，触发器权重保持当前值不变。'}
        confirmName={name}
        confirmLabel="确认删除"
        onConfirm={() =>
          del.mutate(
            { ns, name },
            {
              onSuccess: () => { toast.show('删除金丝雀', name + ' 已删除'); setDelOpen(false); onDeleted(); },
              onError: (err) => { toast.showError('删除金丝雀', getErrMessage(err)); setDelOpen(false); },
            },
          )
        }
        onCancel={() => setDelOpen(false)}
      />
    </section>
  );
}

/* ---------------- create wizard ---------------- */

interface WizardState {
  trigger: string;
  oldVersion: string;
  newVersion: string;
  weightincrement: number;
  duration: string;
  failurethreshold: number;
  failureType: 'failure' | 'timeout';
}

function CreateWizard({ open, onClose }: { open: boolean; onClose: () => void }): ReactElement | null {
  const toast = useToast();
  const create = useCanaryCreate();
  const triggers = useList<H>('httptriggers');
  const [step, setStep] = useState(1);
  const [form, setForm] = useState<WizardState>({ trigger: '', oldVersion: '', newVersion: '', weightincrement: 10, duration: '2m', failurethreshold: 3, failureType: 'failure' });

  const chosen = (triggers.data ?? []).find((t) => t.metadata.namespace + '/' + t.metadata.name === form.trigger);
  const fnNs = chosen?.spec.functionref?.namespace ?? chosen?.metadata.namespace ?? '';
  const fnName = chosen?.spec.functionref?.name ?? '';
  const versions = useFunctionVersionList(fnNs, fnName);

  const set = (patch: Partial<WizardState>): void => setForm((f) => ({ ...f, ...patch }));

  const validate = (): string | null => {
    if (!form.trigger) return '必须选择 HTTP 触发器';
    if (!form.oldVersion || !form.newVersion) return '必须选择稳定版与金丝雀版';
    if (form.oldVersion === form.newVersion) return '稳定版与金丝雀版不能相同';
    if (!(form.weightincrement > 0 && form.weightincrement <= 100)) return '权重步进须在 1–100 之间';
    if (!/^[0-9]+(ms|s|m|h|d)$/.test(form.duration)) return '步进间隔必须是 Go duration 字符串，如 30s / 2m / 1h';
    if (!(form.failurethreshold > 0)) return '失败阈值必须为正整数';
    if (!['failure', 'timeout'].includes(form.failureType)) return '失败类型仅限 failure / timeout';
    return null;
  };

  const submit = (): void => {
    const err = validate();
    if (err) { toast.show('创建金丝雀', err, 'error'); return; }
    const [tns, ...rest] = form.trigger.split('/');
    create.mutate(
      {
        ns: tns,
        // CRD envelope：后端读 obj.metadata.namespace 并将整个 obj 交给 crd_create
        body: {
          apiVersion: 'fission.io/v1',
          kind: 'CanaryConfig',
          metadata: {
            name: (rest.join('/') || 'canary').split('/').pop() + '-canary',
            namespace: tns,
          },
          spec: {
            trigger: rest.join('/'),
            oldfunction: form.oldVersion,
            newfunction: form.newVersion,
            weightincrement: form.weightincrement,
            duration: form.duration,
            failurethreshold: form.failurethreshold,
            failureType: form.failureType,
          },
        },
      },
      {
        onSuccess: () => { toast.show('创建金丝雀', '策略已提交'); setStep(1); setForm((f) => ({ ...f, trigger: '', oldVersion: '', newVersion: '' })); onClose(); },
        onError: (e) => toast.showError('创建金丝雀', getErrMessage(e)),
      },
    );
  };

  const versions2 = versions.data?.versions ?? [];

  return (
    <Drawer open={open} onClose={onClose} title="创建金丝雀策略" subtitle="CANARY / WIZARD">
      <div className="cap" style={{ marginBottom: 12 }}>步骤 {step}/3 — {step === 1 ? '选择触发器' : step === 2 ? '选择版本对' : '步进与失败策略'}</div>

      {step === 1 ? (
        <div className="field">
          <span>HTTP 触发器 *</span>
          <select value={form.trigger} onChange={(e) => set({ trigger: e.target.value })}>
            <option value="" disabled>选择触发器…</option>
            {(triggers.data ?? []).map((t) => (
              <option key={t.metadata.namespace + '/' + t.metadata.name} value={t.metadata.namespace + '/' + t.metadata.name}>
                {t.metadata.namespace}/{t.metadata.name} → {t.spec.functionref?.name}
              </option>
            ))}
          </select>
          <div className="cap">金丝雀权重挂在触发器上，故必须经由 HTTPTrigger。</div>
        </div>
      ) : null}

      {step === 2 ? (
        <>
          <div className="field">
            <span>稳定版（旧）*</span>
            <select value={form.oldVersion} onChange={(e) => set({ oldVersion: e.target.value })}>
              <option value="" disabled>选择版本…</option>
              {versions2.map((v: VersionInfo) => <option key={v.name} value={v.name}>{v.name}</option>)}
            </select>
          </div>
          <div className="field">
            <span>金丝雀版（新）*</span>
            <select value={form.newVersion} onChange={(e) => set({ newVersion: e.target.value })}>
              <option value="" disabled>选择版本…</option>
              {versions2.map((v: VersionInfo) => <option key={v.name} value={v.name}>{v.name}</option>)}
            </select>
          </div>
          {versions.error ? <div className="banner" style={{ margin: 0 }}>▲ 版本列表不可达：{getErrMessage(versions.error)}</div> : null}
        </>
      ) : null}

      {step === 3 ? (
        <>
          <div className="form-row">
            <div className="field">
              <span>权重步进 % *</span>
              <input type="number" min={1} max={100} value={form.weightincrement} onChange={(e) => set({ weightincrement: Number(e.target.value) })} />
            </div>
            <div className="field">
              <span>步进间隔（秒）*</span>
              <input value={form.duration} placeholder="2m" onChange={(e) => set({ duration: e.target.value })} />
              <div className="cap">Go duration 字符串（如 30s / 2m / 1h），Fission 默认 2m。</div>
            </div>
          </div>
          <div className="form-row">
            <div className="field">
              <span>失败阈值 *</span>
              <input type="number" min={1} value={form.failurethreshold} onChange={(e) => set({ failurethreshold: Number(e.target.value) })} />
            </div>
            <div className="field">
              <span>失败类型 *</span>
              <select value={form.failureType} onChange={(e) => set({ failureType: e.target.value as WizardState['failureType'] })}>
                <option value="failure">failure（请求失败）</option>
                <option value="timeout">timeout（超时）</option>
              </select>
            </div>
          </div>
          <div className="cap">
            确认生成：{form.trigger}　{form.oldVersion} → {form.newVersion}　每 {form.duration} +{form.weightincrement}%，{form.failureType} 连续 {form.failurethreshold} 次即中止。
          </div>
        </>
      ) : null}

      <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
        {step > 1 ? <button type="button" className="btn" onClick={() => setStep(step - 1)}>上一步</button> : null}
        {step < 3 ? (
          <button type="button" className="btn pri" disabled={(step === 1 && !form.trigger)} onClick={() => setStep(step + 1)}>下一步</button>
        ) : (
          <button type="button" className="btn pri" disabled={create.isPending} onClick={submit}>{create.isPending ? '提交中…' : '确认生成'}</button>
        )}
        <button type="button" className="btn" onClick={onClose}>取消</button>
      </div>
    </Drawer>
  );
}

/* ---------------- page ---------------- */

export default function Canary(): ReactElement {
  const configs = useCanaryConfigs();
  const [selected, setSelected] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);

  const list = configs.data ?? [];
  const cfg = useMemo(
    () => list.find((c) => (c.metadata?.namespace ?? 'default') + '/' + (c.metadata?.name ?? '') === selected) ?? list[0] ?? null,
    [list, selected],
  );

  return (
    <div className="shell" style={{ flex: 1, minHeight: 0 }}>
      <div className="list-pane">
        <div className="pane-h">
          金丝雀策略
          <span>{list.length} · 状态 5s 轮询</span>
          <span className="actions">
            <button type="button" className="btn sm pri" onClick={() => setWizardOpen(true)}>+ 创建</button>
          </span>
        </div>
        <div className="list-scroll">
          {configs.isError && errStatus(configs.error) === 403 ? (
            <div className="banner">▲ 权限不足（403）：当前账号无 canaryconfigs 读取权限。</div>
          ) : configs.isError ? (
            <div className="banner">▲ 金丝雀列表不可达：{getErrMessage(configs.error)}</div>
          ) : configs.isLoading ? (
            <div className="empty">加载中…</div>
          ) : list.length === 0 ? (
            <div className="empty">暂无金丝雀策略</div>
          ) : (
            <table className="obs-table">
              <thead>
                <tr><th>名称</th><th>触发器</th><th>版本对</th><th>状态</th></tr>
              </thead>
              <tbody>
                {list.map((c) => {
                  const key = (c.metadata?.namespace ?? 'default') + '/' + (c.metadata?.name ?? '');
                  return (
                    <tr key={key} className={cfg && key === (cfg.metadata?.namespace ?? 'default') + '/' + (cfg.metadata?.name ?? '') ? 'row-sel' : undefined} onClick={() => setSelected(key)}>
                      <td className="fn-name">{c.metadata?.name ?? '—'}</td>
                      <td className="mono">{c.spec?.trigger ?? '—'}</td>
                      <td className="mono" style={{ fontSize: 11 }}>{c.spec?.oldfunction ?? '—'} → {c.spec?.newfunction ?? '—'}</td>
                      <td><CanaryCell ns={c.metadata?.namespace ?? 'default'} name={c.metadata?.name ?? ''} threshold={c.spec?.failurethreshold} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {cfg ? <CanaryDetail cfg={cfg} onDeleted={() => setSelected(null)} /> : (
        <section className="inspector">
          <div className="empty" style={{ marginTop: 64 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 18, letterSpacing: 1, marginBottom: 8 }}>选择一条金丝雀策略</div>
            <p>查看实时权重、失败计数，并执行回滚 / 晋级干预。</p>
          </div>
        </section>
      )}

      <CreateWizard open={wizardOpen} onClose={() => setWizardOpen(false)} />
    </div>
  );
}

function CanaryCell({ ns, name, threshold }: { ns: string; name: string; threshold?: number }): ReactElement {
  const status = useCanaryStatus(ns, name);
  if (status.isError && errStatus(status.error) !== 501 && errStatus(status.error) !== 404) {
    return <span className="mono muted" title={getErrMessage(status.error)}>轮询失败</span>;
  }
  const tone = phaseTone(status.data);
  const hit = typeof threshold === 'number' && typeof status.data?.failures === 'number' && status.data.failures >= threshold;
  return <StatusBadge status={tone.status} tone={hit ? 'error' : tone.tone} qualifier={hit ? '▲ ' + status.data?.failures + ' 失败' : undefined} />;
}