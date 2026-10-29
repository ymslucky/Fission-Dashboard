import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { server } from '../test/msw';
import { http, HttpResponse } from 'msw';
import { COLD_START_WARNING } from '../lib/versions';

/** versions 端点：ensure_feature 501（server/routers/versions.py + k8s.ensure_feature 实际响应键）。 */
function mockVersionsUnavailable(): void {
  server.use(
    http.get('*/api/functions/fission/quote/versions', () =>
      new HttpResponse(
        JSON.stringify({ detail: { message: '集群未启用 functionversions CRD', feature: 'functionversions' } }),
        { status: 501, headers: { 'Content-Type': 'application/json' } },
      ),
    ),
  );
}

/** versions 端点：CRD 存在形态（versions.py list_versions 实际 return）。 */
const versionsBody = {
  versions: [
    { name: 'v2', created: '2026-01-02T00:00:00Z', function: 'quote-v2', functionExists: false },
    { name: 'v1', created: '2026-01-01T00:00:00Z', function: 'quote', functionExists: true },
  ],
  aliases: [{ name: 'prod', target: 'v1', created: '2026-01-01T00:00:00Z' }],
};

/** J4 — 版本回滚旅程：降级横幅 → 时间线/别名高亮 → 回滚确认（冷启动警示）→ POST + toast。 */
describe('J4 · 版本回滚旅程', () => {
  it('case1 线上无 CRD：versions 501 → 降级横幅出现', async () => {
    // Given：集群未安装 functionversions/functionalias CRD → ensure_feature 抛 501
    mockVersionsUnavailable();
    const { renderApp } = await import('./helpers');
    renderApp(['/versions']);

    // When：版本页真实挂载并请求 versions 端点（函数选择器自动选中 fission/quote）
    // Then：降级横幅（Versions.tsx 实际文案为「版本端点未上线（501）」；后端
    // ensure_feature 的「集群未启用 … CRD」消息不在该横幅中展示）
    expect(await screen.findByText(/版本端点未上线（501）/)).toBeInTheDocument();
    expect(screen.getByText('暂无版本记录')).toBeInTheDocument();
  });

  it('case2 CRD 存在：时间线两行、prod→v1 高亮 → 回滚 v2 → 确认含冷启动警示 → POST 200 → toast', async () => {
    // Given：versions 端点返回两版本 + 别名 prod 指向 v1；rollback 200（versions.py rollback 实际 return）
    let rollbackBody: Record<string, unknown> | null = null;
    server.use(
      http.get('*/api/functions/fission/quote/versions', () => HttpResponse.json(versionsBody)),
      http.post('*/api/functions/fission/quote/rollback', async ({ request }) => {
        rollbackBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ok: true, alias: 'prod', version: 'v2', target: 'quote-v2',
          coldStartWarning: COLD_START_WARNING,
        });
      }),
    );
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/versions']);
    await screen.findByText('v2'); // 时间线渲染完成

    // When：阅读时间线（v2 最新 / v1 为别名指向），点 v2 行「回滚到此版」并确认
    const items = await screen.findAllByRole('listitem');
    const v2Row = items.find((li) => within(li).queryByText('v2')) as HTMLElement;
    const v1Row = items.find((li) => within(li).queryByText('v1')) as HTMLElement;
    expect(within(v1Row).getByText(/别名 prod/)).toBeInTheDocument(); // 别名指向高亮
    expect(within(v1Row).getByText(/当前指向/)).toBeInTheDocument();
    expect(within(v1Row).getByText('FUNCTION OK')).toBeInTheDocument();
    expect(within(v2Row).getByText('FUNCTION 缺失')).toBeInTheDocument();
    expect(within(v1Row).getByRole('button', { name: '回滚到此版' })).toBeDisabled();
    await user.click(within(v2Row).getByRole('button', { name: '回滚到此版' }));

    // Then：ConfirmDialog 出现且含冷启动警示文案，输入版本名确认后 POST + toast
    const dialog = await screen.findByRole('alertdialog', { name: '回滚 · v2' });
    expect(within(dialog).getByText(/目标版本若无 warm Pod/)).toBeInTheDocument();
    await user.type(within(dialog).getByPlaceholderText('v2'), 'v2');
    await user.click(within(dialog).getByRole('button', { name: '确认回滚' }));
    expect(rollbackBody).toEqual({ alias: 'prod', toVersion: 'v2' });
    // toast 文案即 POST 响应里的 coldStartWarning（rollbackGate/COLD_START_WARNING 实际字符串）
    expect(await screen.findByText(/目标版本若无 warm Pod/)).toBeInTheDocument();
  });
});
