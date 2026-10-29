import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { server } from '../test/msw';
import { http, HttpResponse } from 'msw';

/** /audit 处理器：记录每次请求的 limit，按 limit 返回不同条数（真实审计字段形态）。 */
function installAudit(mutate?: () => void): { limits: string[] } {
  const limits: string[] = [];
  server.use(
    // TenantCounts 的命名空间级列表（/api/resources/{kind}/fission）
    ...['functions', 'packages', 'environments', 'httptriggers', 'timetriggers'].map((k) =>
      http.get('*/api/resources/' + k + '/fission', () => HttpResponse.json({ items: [] }))),
    http.get('*/api/audit', ({ request }) => {
      const url = new URL(request.url);
      limits.push(url.searchParams.get('limit') ?? '');
      mutate?.();
      const items = [
        {
          ts: '2026-01-02T03:04:05Z', user: 'tester', action: 'create',
          kind: 'Function', ns: 'fission', name: 'quote', summary: '创建函数 quote',
        },
        {
          ts: '2026-01-01T09:30:00Z', user: 'alice', action: 'delete',
          kind: 'HTTPTrigger', ns: 'fission', name: 'old-http', summary: '删除触发器 old-http',
        },
      ];
      return HttpResponse.json({ items: url.searchParams.get('limit') === '50' ? items.slice(0, 1) : items });
    }),
  );
  return { limits };
}

/** J8 — 审计旅程：流水表渲染 / limit 切换重新请求 / 403 权限横幅。 */
describe('J8 · 审计旅程', () => {
  it('case1 happy：表格行渲染（时间格式化、资源列拼接 kind+ns/name），limit 切换 50/200 重新请求', async () => {
    // Given：/audit 返回两条真实字段流水，默认 limit=200
    const { limits } = installAudit();
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/audit']);

    // When：审计页挂载后把「条数上限」切到 50，再切回 200
    const row1 = await screen.findByText('创建函数 quote');
    expect(row1).toBeInTheDocument();
    const expectedTs = new Date('2026-01-02T03:04:05Z').toLocaleString('zh-CN', { hour12: false });
    expect(screen.getByText(expectedTs)).toBeInTheDocument();
    expect(screen.getByText('Function fission/quote')).toBeInTheDocument();
    expect(screen.getByText('HTTPTrigger fission/old-http')).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();

    const limitField = screen.getByText('条数上限').closest('.field') as HTMLElement;
    const limitSel = within(limitField).getByRole('combobox');
    await user.selectOptions(limitSel, '50');
    await screen.findByText(/1 条 · 最新在上/);
    await user.selectOptions(limitSel, '200');
    await screen.findByText(/2 条 · 最新在上/);

    // Then：每次切换都带新 limit 重新请求
    expect(limits).toEqual(['200', '50', '200']);
  });

  it('case2 403：权限横幅出现', async () => {
    // Given：/audit 返回 403（RBAC 拒绝）
    installAudit();
    server.use(
      http.get('*/api/audit', () =>
        new HttpResponse(JSON.stringify({ detail: { message: 'forbidden' } }),
          { status: 403, headers: { 'Content-Type': 'application/json' } })),
    );
    const { renderApp } = await import('./helpers');
    renderApp(['/audit']);

    // When：审计页挂载并请求失败
    // Then：权限横幅（Audit.tsx 实际文案）
    expect(await screen.findByText(/权限不足（403）：当前账号无审计读取权限/)).toBeInTheDocument();
  });
});
