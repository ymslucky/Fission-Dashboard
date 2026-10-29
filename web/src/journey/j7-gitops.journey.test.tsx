import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { server } from '../test/msw';
import { http, HttpResponse } from 'msw';

let applyBodies: Array<Record<string, unknown>> = [];
let exportBodies: Array<Record<string, unknown>> = [];

function notFound() {
  return new HttpResponse(JSON.stringify({ detail: { message: 'source not found' } }),
    { status: 404, headers: { 'Content-Type': 'application/json' } });
}

/** case1：源未配置 —— 所有 gitops 读端点 404 source not found（misc.py→gitops.get_source）。 */
function installSourceMissing(): void {
  applyBodies = [];
  exportBodies = [];
  server.use(
    http.get('*/api/gitops/repo/status', () => notFound()),
    http.get('*/api/gitops/repo/tree', () => notFound()),
    http.get('*/api/gitops/repo/plan', () => notFound()),
  );
}

/** case2/case3：源已配置 —— status 五格计数 / tree / plan（gitops.py 实际 return 形态）。 */
function installSourceReady(): void {
  applyBodies = [];
  exportBodies = [];
  server.use(
    http.get('*/api/gitops/repo/status', () =>
      HttpResponse.json({ create: 1, update: 0, delete: 1, conflict: 1, unchanged: 1 })),
    http.get('*/api/gitops/repo/tree', () => HttpResponse.json({
      repo: 'github.com/acme/fn', branch: 'main',
      files: [{ path: 'functions/quote.yaml', size: 120 }],
      resources: [
        { path: 'functions/quote.yaml', kind: 'Function', ns: 'fission', name: 'quote', state: 'conflict' },
      ],
    })),
    http.get('*/api/gitops/repo/plan', () => HttpResponse.json({
      repo: 'github.com/acme/fn', branch: 'main', files: 1,
      create: [{ path: 'functions/newfn.yaml', kind: 'Function', ns: 'fission', name: 'newfn', key: 'Function/fission/newfn' }],
      update: [],
      delete: [{ kind: 'Function', ns: 'fission', name: 'oldfn', key: 'Function/fission/oldfn' }],
      conflict: [{ path: 'functions/quote.yaml', kind: 'Function', ns: 'fission', name: 'quote', key: 'Function/fission/quote', hint: '仓库与集群均有修改' }],
      unchanged: [{ kind: 'Function', ns: 'fission', name: 'keep', key: 'Function/fission/keep' }],
    })),
    http.post('*/api/gitops/repo/apply', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      applyBodies.push(body);
      return HttpResponse.json({ created: 1, updated: 0, deleted: 0, skipped: 0, errors: [] });
    }),
    http.post('*/api/gitops/repo/export', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      exportBodies.push(body);
      // gitops_export 实际 return：gh_commit 结果 + changed 计数
      return HttpResponse.json({ commit: 'abc1234', prUrl: 'https://github.com/acme/fn/pull/12', changed: 1 });
    }),
  );
}

/** J7 — GitOps 旅程：源未配置降级 → plan/冲突选边/APPLY → 导出。 */
describe('J7 · GitOps 旅程', () => {
  it('case1 源未配置：plan 404 source not found → 降级横幅且 APPLY 禁用', async () => {
    // Given：sources 配置里没有 repo 源 → get_source 抛 404 source not found
    installSourceMissing();
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/gitops']);

    // When：GitOps 页挂载并请求 status/tree/plan
    // Then：404/501 降级横幅出现，plan 失败横幅带 source not found，APPLY 禁用
    expect(await screen.findByText(/GitOps 端点未上线（501）/)).toBeInTheDocument();
    expect(screen.getByText(/plan 失败：source not found/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '应用 APPLY' })).toBeDisabled();
    void user;
  });

  it('case2 配置好：五格计数带 → 冲突选边（以仓库为准）→ APPLY 解锁 → ConfirmDialog → POST apply → toast', async () => {
    // Given：status/plan/tree 均返回真实形态，含 1 个冲突
    installSourceReady();
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/gitops']);

    // When：读计数带与计划区，为冲突点「以仓库为准」，点 APPLY 并确认
    const band = await screen.findByLabelText('GitOps 同步计数观测带');
    for (const label of ['新建 CREATE', '更新 UPDATE', '删除 DELETE', '冲突 CONFLICT', '一致 UNCHANGED']) {
      expect(within(band).getByText(label)).toBeInTheDocument();
    }
    expect(await screen.findByText(/检测到 1 个冲突/)).toBeInTheDocument();
    // 计划区冲突条目：资源名与 hint（树表与计划区同文本，取带 hint 的那个）
    const conflictEntries = await screen.findAllByText('Function fission/quote');
    expect(conflictEntries.length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText(/仓库与集群均有修改/)).toBeInTheDocument();
    const applyBtn = screen.getByRole('button', { name: '应用 APPLY' });
    expect(applyBtn).toBeDisabled(); // 冲突未选边
    await user.click(screen.getByRole('radio', { name: '以仓库为准' }));
    expect(applyBtn).not.toBeDisabled();
    await user.click(applyBtn);

    // Then：ConfirmDialog 摘要含冲突策略，确认后 POST apply 请求体 {deletes,conflicts}，toast 出现
    const dialog = await screen.findByRole('alertdialog', { name: '应用 APPLY' });
    expect(within(dialog).getByText(/冲突策略：Function fission\/quote=repo/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '确认应用' }));
    await screen.findByText(/apply 已提交/);
    expect(applyBodies).toEqual([{ deletes: [], conflicts: { 'Function/fission/quote': 'repo' } }]);
  });

  it('case3 导出：勾选函数 → POST export → 结果文案含 prUrl', async () => {
    // Given：源已配置，导出区列出 fission/quote
    installSourceReady();
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/gitops']);
    await screen.findByText('GitOps 同步');

    // When：勾选 fission/quote，点「导出」
    await user.click(await screen.findByRole('checkbox', { name: 'fission/quote' }));
    await user.click(screen.getByRole('button', { name: '导出' }));

    // Then：POST export 请求体 {mode,resources}；结果文案展示 prUrl（export 实际 return 字段）
    await screen.findByText('结果：https://github.com/acme/fn/pull/12');
    expect(exportBodies).toEqual([{ mode: 'export', resources: ['fission/quote'] }]);
  });
});
