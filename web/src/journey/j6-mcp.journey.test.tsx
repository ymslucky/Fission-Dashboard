import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { server } from '../test/msw';
import { http, HttpResponse } from 'msw';

/** /mcp/tools 元素：按 mcp.py 实际 return {ns,name,toolDescription,inputSchema}。 */
const quoteTool = {
  ns: 'fission',
  name: 'quote',
  toolDescription: 'Quote service tool',
  inputSchema: { type: 'object', properties: { msg: { type: 'string' } }, required: ['msg'] },
};

let toolPutBodies: Array<Record<string, unknown>> = [];
let tokenPostBodies: Array<Record<string, unknown>> = [];
let tokenDeletes: string[] = [];
let tokens: unknown[] = [];

function installMcpHandlers(enabled: boolean, message?: string): void {
  toolPutBodies = [];
  tokenPostBodies = [];
  tokenDeletes = [];
  tokens = [
    { name: 'ci-pipeline', allowedNamespaces: ['fission'], expiresAt: 1_790_000_000, issuedAt: 1_760_000_000, expired: false },
  ];
  server.use(
    http.get('*/api/mcp/config', () =>
      HttpResponse.json(enabled
        ? { enabled: true, source: 'configmap/fission-controller' }
        : { enabled: false, source: 'probe-failed', message })),
    http.get('*/api/mcp/tools', () => HttpResponse.json({ tools: [quoteTool] })),
    http.put('*/api/functions/fission/quote/mcp', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      toolPutBodies.push(body);
      return HttpResponse.json({ ok: true });
    }),
    http.get('*/api/mcp/tokens', () => HttpResponse.json({ tokens })),
    http.post('*/api/mcp/tokens', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      tokenPostBodies.push(body);
      tokens = [...tokens, {
        name: body.name,
        allowedNamespaces: body.allowedNamespaces,
        expiresAt: 1_790_000_000,
        issuedAt: 1_760_000_000,
        expired: false,
      }];
      return HttpResponse.json({ name: body.name, token: 'tok-xyz', expiresAt: 1_790_000_000 }, { status: 201 });
    }),
    http.delete('*/api/mcp/tokens/:name', ({ params }) => {
      const name = String(params.name);
      tokenDeletes.push(name);
      tokens = (tokens as Array<{ name?: string }>).filter((t) => t.name !== name);
      return HttpResponse.json({ ok: true, name });
    }),
  );
}

/** J6 — MCP 旅程：未启用引导 → 工具目录编辑暴露 → token 签发/吊销。 */
describe('J6 · MCP 旅程', () => {
  it('case1 mcp.enabled=false：整页引导面板（未启用横幅 + 启用方法）', async () => {
    // Given：mcp/config 返回 enabled:false + 探测失败 message（mcp.py mcp_config 实际形态）
    installMcpHandlers(false, '未能从集群探测 MCP 配置（fission configmap 无 mcp.enabled），按未启用处理；如已启用请检查 fission-controller/fission-config ConfigMap');
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/mcp']);

    // When：MCP 页挂载读取配置
    // Then：引导面板文案可见，无工具目录表
    expect(await screen.findByText(/MCP 暴露未启用：未能从集群探测 MCP 配置/)).toBeInTheDocument();
    expect(screen.getByText('启用方法')).toBeInTheDocument();
    expect(screen.getByText('--expose-as-mcp=true')).toBeInTheDocument();
    expect(screen.queryByText('MCP EXPOSURE · 已启用')).not.toBeInTheDocument();
    void user;
  });

  it('case2 enabled=true：工具目录表显示已暴露函数 → 展开编辑描述保存 → PUT 200 → toast', async () => {
    // Given：mcp 已启用，/mcp/tools 返回 mcp.py 实际元素形态
    installMcpHandlers(true);
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/mcp']);
    await screen.findByText('MCP EXPOSURE · 已启用');

    // When：点 quote 行展开编辑器，改描述并「保存并暴露」（quote 也出现在 Schema 摘要，取首个单元格）
    const row = (await screen.findAllByText('quote'))[0].closest('tr') as HTMLElement;
    expect(within(row).getByText('Quote service tool')).toBeInTheDocument();
    expect(within(row).getByText('EXPOSED')).toBeInTheDocument();
    expect(within(row).getByText(/msg*/)).toBeInTheDocument(); // schema 摘要 required 标星
    await user.click(within(row).getByText('quote'));
    const desc = await screen.findByPlaceholderText('给 LLM 看的工具描述…');
    await user.clear(desc);
    await user.type(desc, 'Quotes any input string');
    await user.click(screen.getByRole('button', { name: '保存并暴露' }));

    // Then：PUT /functions/fission/quote/mcp 请求体携带新描述与原 schema；toast 出现
    await screen.findByText(/quote 已更新/);
    expect(toolPutBodies).toEqual([{
      enabled: true,
      toolDescription: 'Quotes any input string',
      inputSchema: { type: 'object', properties: { msg: { type: 'string' } }, required: ['msg'] },
    }]);
  });

  it('case3 token：签发（POST）→ 列表新增 → 吊销走 ConfirmDialog → DELETE', async () => {
    // Given：已有 ci-pipeline token，签发表单可用
    installMcpHandlers(true);
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/mcp']);
    await screen.findByText('访问策略 · TOKEN');

    // When：填 name / 勾选命名空间 / 默认 TTL 30 并签发
    expect(await screen.findByText('ci-pipeline')).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText('ci-pipeline'), 'deploy-bot');
    await user.click(screen.getByRole('checkbox', { name: 'fission' }));
    await user.click(screen.getByRole('button', { name: '签发' }));

    // Then：POST 请求体正确，列表出现 deploy-bot
    await screen.findByText('deploy-bot');
    expect(tokenPostBodies).toEqual([{ name: 'deploy-bot', allowedNamespaces: ['fission'], ttlDays: 30 }]);

    // When：点 deploy-bot 行「吊销」，ConfirmDialog 中输入名称确认
    const row = screen.getByText('deploy-bot').closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: '吊销' }));
    const dialog = await screen.findByRole('alertdialog', { name: '吊销 token' });
    expect(within(dialog).getByText(/将吊销 deploy-bot/)).toBeInTheDocument();
    await user.type(within(dialog).getByPlaceholderText('deploy-bot'), 'deploy-bot');
    await user.click(within(dialog).getByRole('button', { name: '确认吊销' }));

    // Then：DELETE /mcp/tokens/deploy-bot 发出，行消失
    await screen.findByText(/deploy-bot 已吊销/);
    expect(tokenDeletes).toEqual(['deploy-bot']);
    await screen.findByText('ci-pipeline');
    expect(screen.queryByText('deploy-bot')).not.toBeInTheDocument();
  });
});
