import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { givenOnQuoteDetail } from './helpers';
import { server } from '../test/msw';
import { http, HttpResponse } from 'msw';

/** 覆盖 httptriggers 列表与 describe.triggers。 */
function mockHttpTriggers(items: unknown[], describeTriggers: unknown[]): void {
  server.use(http.get('*/api/resources/httptriggers', () => HttpResponse.json({ items })));
  server.use(http.get('*/api/functions/fission/quote/describe', () =>
    HttpResponse.json({
      function: { metadata: { name: 'quote', namespace: 'fission' } },
      package: { name: 'quote-pkg', buildStatus: 'succeeded', buildlog: '', object: null },
      pods: { warm: 2, ready: 2 },
      executorType: 'poolmgr',
      invocable: true,
      invocableNote: 'Yes (cold start on first call)',
      triggers: describeTriggers,
      canary: [],
      mcpToolExposed: { enabled: false, toolDescription: '', inputSchema: {} },
    })));
}

/**
 * J3 — 触发器页签旅程：三种 functionref 形态下的触发器→函数匹配
 * （加权命中 / functionref.name 单函数命中 / 空列表空态）。
 */
describe('J3 · 触发器页签旅程', () => {
  it('case1 加权命中：functionweights 含 quote → 触发器列表显示 quote-http（1 行）', async () => {
    // Given：基线 handlers——quote-http 为 function-weights 加权形态（quote:80 / quote-v2:20）
    const user = userEvent.setup();
    await givenOnQuoteDetail(user);

    // When：点击「触发器」页签
    await user.click(screen.getByRole('tab', { name: /触发器/ }));

    // Then：列表含一行 quote-http，详情 GET /quote
    // 页面同时有函数列表表（obs-table）与触发器表（plain），取 DOM 序最后的触发器表
    const tables = await screen.findAllByRole('table');
    const table = tables[tables.length - 1];
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(2); // 表头 + 数据行
    expect(within(table).getByText('quote-http')).toBeInTheDocument();
    expect(within(table).getByText('GET /quote')).toBeInTheDocument();
  });

  it('case2 单函数命中：functionref.name=quote → 同样命中 quote-http', async () => {
    // Given：httptriggers 覆盖为 functionref 单函数形态 {type:'name',name:'quote'}
    mockHttpTriggers(
      [{
        apiVersion: 'fission.io/v1', kind: 'HTTPTrigger',
        metadata: { name: 'quote-http', namespace: 'fission', uid: 'uid-trig-1' },
        spec: { functionref: { type: 'name', name: 'quote' }, relativeurl: '/quote', methods: ['GET'] },
      }],
      [{ kind: 'httptriggers', ns: 'fission', name: 'quote-http', url: '/quote' }],
    );
    const user = userEvent.setup();
    await givenOnQuoteDetail(user);

    // When：点击触发器页签
    await user.click(screen.getByRole('tab', { name: /触发器/ }));

    // Then：同样命中
    const tables = await screen.findAllByRole('table');
    const table = tables[tables.length - 1];
    expect(within(table).getByText('quote-http')).toBeInTheDocument();
  });

  it('case3 空列表：httptriggers 与 describe.triggers 均空 → 空态文案「该函数暂无触发器」', async () => {
    // Given：httptriggers items:[] 且 describe.triggers:[]
    mockHttpTriggers([], []);
    const user = userEvent.setup();
    await givenOnQuoteDetail(user);

    // When：点击「触发器 0」页签
    await user.click(screen.getByRole('tab', { name: /触发器 0/ }));

    // Then：空态文案（Functions.tsx 实际字符串）
    expect(await screen.findByText('该函数暂无触发器')).toBeInTheDocument();
  });
});
