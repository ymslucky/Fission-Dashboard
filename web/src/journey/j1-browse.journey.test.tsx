import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { server } from '../test/msw';
import { http, HttpResponse } from 'msw';
import { quoteDescribe } from '../test/msw';

/**
 * J1 — 浏览函数旅程：总览 → 侧栏「函数」→ 列表 → quote 详情基础信息。
 * 已知陷阱：Overview 点阵 svg 含函数名文本，离开总览须先等到「函数观测表」。
 */
describe('J1 · 浏览函数旅程', () => {
  it('case1 happy：列表出现 quote → 详情显示 READY 徽章 / invocable Yes / MCP 暴露 false', async () => {
    // Given：基线 handlers（describe package.buildStatus=succeeded），用户在 /overview
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/overview']);

    // When：点侧栏「函数」，等列表挂载后点 quote 行进入详情
    await user.click(await screen.findByRole('link', { name: /函数/ }));
    await screen.findByText('函数观测表');
    await user.click(await screen.findByText('quote'));

    // Then：详情基础信息全部可见
    await screen.findByText('DESCRIBE · 构建与运行时');
    expect(screen.getByRole('heading', { name: 'quote' })).toBeInTheDocument();
    // 元信息行（列表页环境列也有 python，取详情 meta 段落）
    const meta = screen.getByText('quote-pkg').closest('p') as HTMLElement;
    expect(meta).toHaveClass('meta');
    expect(meta.textContent).toContain('环境');
    expect(meta.textContent).toContain('python');
    const buildRow = screen.getByText('构建状态').closest('tr') as HTMLElement;
    const badge = within(buildRow).getByText('READY');
    expect(badge).toHaveClass('status-ok'); // succeeded → READY（绿色 ok tone）
    const invocableRow = screen.getByText('invocable').closest('tr') as HTMLElement;
    expect(invocableRow.textContent).toContain('Yes');
    const mcpRow = screen.getByText('MCP 暴露').closest('tr') as HTMLElement;
    expect(mcpRow.textContent).toContain('false');
  });

  it('case2 构建 failed：describe package.buildStatus=failed → 构建状态徽章 ERROR 红色', async () => {
    // Given：describe 覆盖为 package.buildStatus='failed'（真实 Package 构建失败形态）
    server.use(
      http.get('*/api/functions/fission/quote/describe', () =>
        HttpResponse.json({
          ...quoteDescribe,
          package: { name: 'quote-pkg', buildStatus: 'failed', buildlog: 'error: pip resolve failed', object: null },
        }),
      ),
    );
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/overview']);

    // When：同样经真实点击进入 quote 详情
    await user.click(await screen.findByRole('link', { name: /函数/ }));
    await screen.findByText('函数观测表');
    await user.click(await screen.findByText('quote'));

    // Then：构建状态行渲染 ERROR 徽章，且为 error（红）tone
    await screen.findByText('DESCRIBE · 构建与运行时');
    const buildRow = screen.getByText('构建状态').closest('tr') as HTMLElement;
    const badge = within(buildRow).getByText('ERROR');
    expect(badge).toHaveClass('status-error');
  });
});
