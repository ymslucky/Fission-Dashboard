import { useEffect, useState, type ReactElement } from 'react';
import { useList } from '../api/hooks';
import {
  useMcpConfig,
  useMcpTokenCreate,
  useMcpTokenRevoke,
  useMcpTokens,
  useMcpTools,
  useSetFunctionMcp,
} from '../api/extend';
import type { McpTool } from '../api/extend';
import type { Function } from '../api/types';
import { ConfirmDialog, useToast } from '../components';
import { getErrMessage, errStatus } from '../lib/err';

type F = Function & { kind: 'Function' };

function schemaSummary(schema: unknown): string {
  if (!schema) return '—';
  try {
    const obj = typeof schema === 'string' ? (JSON.parse(schema) as Record<string, unknown>) : (schema as Record<string, unknown>);
    const props = obj.properties as Record<string, unknown> | undefined;
    const keys = props ? Object.keys(props) : [];
    const req = Array.isArray(obj.required) ? (obj.required as string[]) : [];
    if (keys.length === 0) return 'type ' + String(obj.type ?? '?') + ' · 无字段';
    return keys.map((k) => k + (req.includes(k) ? '*' : '')).join(', ');
  } catch {
    return String(typeof schema === 'string' ? schema.slice(0, 40) : '…');
  }
}

function Status({ enabled }: { enabled: boolean }): ReactElement {
  return enabled
    ? <span className="status status-ok"><span className="sig sig-dot" />EXPOSED</span>
    : <span className="status status-neutral"><span className="sig sig-dot" />OFF</span>;
}

function ToolRow({ tool }: { tool: McpTool }): ReactElement {
  const toast = useToast();
  const mut = useSetFunctionMcp();
  const [open, setOpen] = useState(false);
  const [desc, setDesc] = useState('');
  const [schemaText, setSchemaText] = useState('');
  const [schemaErr, setSchemaErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (open && !loaded) {
      setDesc(tool.toolDescription ?? '');
      const s = tool.inputSchema;
      setSchemaText(s ? JSON.stringify(s, null, 2) : '');
      setLoaded(true);
    }
  }, [open, loaded, tool]);

  const ns = tool.ns ?? 'default';
  const name = tool.name ?? '';

  const save = (enabled: boolean): void => {
    let schema: Record<string, unknown> | undefined;
    if (schemaText.trim()) {
      try {
        schema = JSON.parse(schemaText) as Record<string, unknown>;
        setSchemaErr(null);
      } catch (e) {
        setSchemaErr('JSON Schema 解析失败：' + getErrMessage(e));
        return;
      }
    }
    mut.mutate(
      { ns, name, body: { enabled, toolDescription: desc, inputSchema: schema } },
      {
        onSuccess: () => toast.show('MCP 暴露', name + (enabled ? ' 已更新' : ' 已关闭暴露')),
        onError: (err) => toast.showError('MCP 暴露 ' + name, getErrMessage(err)),
      },
    );
  };

  return (
    <>
      <tr onClick={() => setOpen((o) => !o)} style={{ cursor: 'pointer' }}>
        <td className="fn-name">{name}</td>
        <td className="mono">{ns}</td>
        <td style={{ maxWidth: 320, whiteSpace: 'normal' }}>{tool.toolDescription || <span className="muted">（未填写描述）</span>}</td>
        <td className="mono" style={{ fontSize: 11 }}>{schemaSummary(tool.inputSchema)}</td>
        {/* /mcp/tools 列出的函数即已暴露（BE 只返回暴露项），无独立 enabled 字段 */}
        <td><Status enabled /></td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={5} style={{ background: 'var(--paper-raise)' }}>
            <div style={{ padding: '8px 4px' }}>
              <div className="field">
                <span>Tool Description</span>
                <textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="给 LLM 看的工具描述…" />
              </div>
              <div className="field">
                <span>Input Schema（JSON）</span>
                <textarea rows={6} className="mono" style={{ fontSize: 12 }} value={schemaText} onChange={(e) => { setSchemaText(e.target.value); setSchemaErr(null); }} placeholder={'{ "type": "object", "properties": { … } }'} />
              </div>
              {schemaErr ? <div className="banner" style={{ margin: '0 0 8px' }}>▲ {schemaErr}</div> : null}
              <div style={{ display: 'flex', gap: 10 }}>
                <button type="button" className="btn sm pri" disabled={mut.isPending} onClick={() => save(true)}>保存并暴露</button>
                <button type="button" className="btn sm" disabled={mut.isPending} onClick={() => save(false)}>关闭暴露</button>
                <button type="button" className="btn sm" onClick={() => { setLoaded(false); setOpen(false); }}>收起</button>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function TokenPanel(): ReactElement {
  const toast = useToast();
  const tokens = useMcpTokens(true);
  const create = useMcpTokenCreate();
  const revoke = useMcpTokenRevoke();
  const functions = useList<F>('functions');

  const [name, setName] = useState('');
  const [ttl, setTtl] = useState(30);
  const [nss, setNss] = useState<string[]>([]);
  const [revokeName, setRevokeName] = useState<string | null>(null);

  const namespaces = Array.from(new Set((functions.data ?? []).map((f) => f.metadata.namespace))).sort();

  const submit = (): void => {
    if (!name.trim()) { toast.show('签发 token', '名称必填', 'error'); return; }
    if (!(ttl > 0)) { toast.show('签发 token', 'TTL 必须为正整数天', 'error'); return; }
    create.mutate(
      { name: name.trim(), allowedNamespaces: nss, ttlDays: ttl },
      {
        onSuccess: () => { toast.show('签发 token', name + ' 已签发，有效期 ' + ttl + ' 天'); setName(''); setNss([]); },
        onError: (err) => toast.showError('签发 token', getErrMessage(err)),
      },
    );
  };

  return (
    <section style={{ marginTop: 20 }}>
      <div className="pane-h" style={{ paddingLeft: 0 }}>访问策略 · TOKEN<span>签发 / 吊销</span></div>
      <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <table className="plain" style={{ flex: '1 1 420px' }}>
          <thead><tr><th>名称</th><th>范围</th><th>过期</th><th></th></tr></thead>
          <tbody>
            {tokens.isLoading ? <tr><td colSpan={4} className="empty">加载中…</td></tr> : null}
            {tokens.error ? <tr><td colSpan={4}><span className="banner" style={{ margin: 0, display: 'inline-block' }}>▲ token 列表不可达：{getErrMessage(tokens.error)}</span></td></tr> : null}
            {!tokens.isLoading && !tokens.error && (tokens.data ?? []).length === 0 ? <tr><td colSpan={4} className="empty">尚无 token</td></tr> : null}
            {(tokens.data ?? []).map((t) => (
              <tr key={t.name}>
                <td className="mono fn-name">{t.name}</td>
                <td className="mono" style={{ fontSize: 11 }}>{(t.allowedNamespaces ?? []).join(', ') || '—'}</td>
                <td className="num" style={{ fontSize: 11 }}>
                  {typeof t.expiresAt === 'number' ? new Date(t.expiresAt * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'}
                  {t.expired ? <span className="status status-error" style={{ marginLeft: 6 }}><span className="sig sig-dot" />已过期</span> : null}
                </td>
                <td><button type="button" className="btn sm danger" onClick={() => setRevokeName(t.name ?? null)}>吊销</button></td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="obs-card" style={{ flex: '0 1 320px', margin: 0 }}>
          <h3>签发新 TOKEN</h3>
          <div className="field">
            <span>名称 *</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="ci-pipeline" />
          </div>
          <div className="field">
            <span>允许命名空间（多选）</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '4px 0' }}>
              {namespaces.map((n) => (
                <label key={n} style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12 }}>
                  <input
                    type="checkbox"
                    checked={nss.includes(n)}
                    onChange={(e) => setNss((old) => (e.target.checked ? [...old, n] : old.filter((x) => x !== n)))}
                  />
                  <span className="mono">{n}</span>
                </label>
              ))}
            </div>
            <div className="cap">留空 = 不限制命名空间。</div>
          </div>
          <div className="field">
            <span>TTL（天）*</span>
            <input type="number" min={1} value={ttl} onChange={(e) => setTtl(Number(e.target.value))} />
          </div>
          <button type="button" className="btn pri sm" disabled={create.isPending} onClick={submit}>{create.isPending ? '签发中…' : '签发'}</button>
        </div>
      </div>

      <ConfirmDialog
        open={revokeName !== null}
        title="吊销 token"
        message={'将吊销 ' + (revokeName ?? '') + '，持有该 token 的客户端将立即失去 MCP 访问。'}
        confirmName={revokeName ?? undefined}
        confirmLabel="确认吊销"
        onConfirm={() =>
          revoke.mutate(
            { name: revokeName ?? '' },
            {
              onSuccess: () => { toast.show('吊销 token', revokeName + ' 已吊销'); setRevokeName(null); },
              onError: (err) => { toast.showError('吊销 token', getErrMessage(err)); setRevokeName(null); },
            },
          )
        }
        onCancel={() => setRevokeName(null)}
      />
    </section>
  );
}

function McpCatalog(): ReactElement {
  const tools = useMcpTools(true);

  return (
    <main className="page page-pad">
      <div className="pane-h">MCP 目录<span>MCP EXPOSURE · 已启用</span></div>
      {tools.isError && errStatus(tools.error) === 403 ? (
        <div className="banner">▲ 权限不足（403）：无 /api/mcp/tools 读取权限。</div>
      ) : tools.isError ? (
        <div className="banner">▲ MCP 工具列表不可达：{getErrMessage(tools.error)}</div>
      ) : tools.isLoading ? (
        <div className="empty">加载中…</div>
      ) : (tools.data ?? []).length === 0 ? (
        <div className="empty">尚无已暴露函数。</div>
      ) : (
        <table className="obs-table">
          <thead>
            <tr><th>名称</th><th>命名空间</th><th>描述</th><th>Schema 摘要</th><th>状态</th></tr>
          </thead>
          <tbody>
            {(tools.data ?? []).map((t, i) => <ToolRow key={(t.name ?? '') + i} tool={t} />)}
          </tbody>
        </table>
      )}
      <TokenPanel />
    </main>
  );
}

export default function Mcp(): ReactElement {
  const config = useMcpConfig();

  if (config.isLoading) {
    return <main className="page page-pad"><div className="empty">加载中…</div></main>;
  }

  if (config.error && errStatus(config.error) === 403) {
    return (
      <main className="page page-pad">
        <div className="banner">▲ 权限不足（403）：当前账号无 /api/mcp/config 读取权限。</div>
      </main>
    );
  }

  if (config.error) {
    return (
      <main className="page page-pad">
        <div className="banner">▲ MCP 配置不可达：{getErrMessage(config.error)}</div>
      </main>
    );
  }

  if (!config.data?.enabled) {
    return (
      <main className="page page-pad">
        <div className="pane-h">MCP 目录<span>MCP EXPOSURE · 功能未启用</span></div>
        <div className="banner" style={{ marginBottom: 16 }}>
          ▲ MCP 暴露未启用{config.data?.message ? '：' + config.data.message : ''}
        </div>
        <div className="obs-card" style={{ maxWidth: 640 }}>
          <h3>启用方法</h3>
          <p style={{ fontSize: 13, lineHeight: 1.8 }}>
            在 Fission 控制器（<span className="mono">fission-controller</span> Deployment）追加启动参数：
          </p>
          <pre className="mono" style={{ background: 'var(--paper-sink)', border: '1px solid var(--hairline)', padding: '8px 12px', fontSize: 12, overflow: 'auto' }}>
{`--expose-as-mcp=true`}
          </pre>
          <p style={{ fontSize: 12, color: 'var(--gray-teal)' }}>
            即 Helm values：<span className="mono">controller.exposeAsMcp=true</span>。启用后本页将列出全部 MCP 工具，并提供描述 / Schema / Token 管理。
          </p>
        </div>
      </main>
    );
  }

  return <McpCatalog />;
}
