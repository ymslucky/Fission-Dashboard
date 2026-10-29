import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { server, quoteFunction, quoteHttpTriggerWeighted } from '../test/msw';
import { http, HttpResponse } from 'msw';

/** Canary 页 handlers：两个 httptrigger（一个 functionweights 型）+ 可变 canaryconfigs。 */
const quoteV2Function = {
  ...quoteFunction,
  metadata: { ...quoteFunction.metadata, name: 'quote-v2', uid: 'uid-quote-2' },
};
void quoteV2Function; // 函数列表走基线 handler 时保留引子；本页断言不直接消费

/** functionref.name 型触发器：向导经它取版本列表（functionweights 型 functionref.name 为空）。 */
const quoteEntryTrigger = {
  apiVersion: 'fission.io/v1',
  kind: 'HTTPTrigger',
  metadata: { name: 'quote-entry', namespace: 'fission', uid: 'uid-trig-2' },
  spec: { functionref: { type: 'name', name: 'quote' }, relativeurl: '/entry', methods: ['GET'] },
};

const initialConfig = {
  apiVersion: 'fission.io/v1',
  kind: 'CanaryConfig',
  metadata: { name: 'quote-rollout', namespace: 'fission', uid: 'uid-canary-1' },
  spec: {
    trigger: 'quote-http', oldfunction: 'quote', newfunction: 'quote-v2',
    weightincrement: 10, duration: '2m', failurethreshold: 3, failureType: 'failure',
  },
};

let configs: unknown[] = [];
let createdPostBodies: unknown[] = [];
let weightPutBodies: Record<string, number>[] = [];

function installCanaryHandlers(): void {
  configs = [initialConfig];
  createdPostBodies = [];
  weightPutBodies = [];
  server.use(
    http.get('*/api/resources/httptriggers', () =>
      HttpResponse.json({ items: [quoteHttpTriggerWeighted, quoteEntryTrigger] })),
    http.get('*/api/canaryconfigs', () => HttpResponse.json({ items: configs })),
    http.post('*/api/canaryconfigs', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      createdPostBodies.push(body);
      configs = [...configs, body];
      return HttpResponse.json(body, { status: 201 });
    }),
    http.get('*/api/canaryconfigs/:ns/:name/status', ({ params }) => {
      const name = String(params.name);
      if (name === 'quote-rollout') {
        return HttpResponse.json({
          name, ns: params.ns, stage: 'progressing', status: {},
          functionWeights: { quote: 70, 'quote-v2': 30 }, failures: 1,
          weightincrement: 10, duration: '2m', failurethreshold: 3, failureType: 'failure',
        });
      }
      return HttpResponse.json({ name, ns: params.ns, stage: 'unknown', status: {}, functionWeights: null, failures: 0 });
    }),
    http.put('*/api/httptriggers/:ns/:name/weights', async ({ request }) => {
      const body = (await request.json()) as { weights: Record<string, number> };
      weightPutBodies.push(body.weights);
      return HttpResponse.json({ ok: true, functionWeights: body.weights });
    }),
    http.get('*/api/functions/fission/quote/versions', () => HttpResponse.json({
      versions: [
        { name: 'quote', created: '2026-01-01T00:00:00Z', functionExists: true },
        { name: 'quote-v2', created: '2026-01-02T00:00:00Z', functionExists: true },
      ],
      aliases: [],
    })),
  );
}

/** 向导字段定位：field div = label span + 控件。 */
function fieldOf(label: string): HTMLElement {
  return screen.getByText(label).closest('.field') as HTMLElement;
}

/** 走完向导前两步（trigger → 版本对），停在第三步。 */
async function gotoStep3(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole('button', { name: '+ 创建' }));
  await screen.findByText('创建金丝雀策略');
  await user.selectOptions(within(fieldOf('HTTP 触发器 *')).getByRole('combobox'), screen.getByRole('option', { name: /quote-entry/ }));
  await user.click(screen.getByRole('button', { name: '下一步' }));
  await screen.findByText('稳定版（旧）*');
  await user.selectOptions(within(fieldOf('稳定版（旧）*')).getByRole('combobox'), within(fieldOf('稳定版（旧）*')).getByRole('option', { name: 'quote' }));
  await user.selectOptions(within(fieldOf('金丝雀版（新）*')).getByRole('combobox'), within(fieldOf('金丝雀版（新）*')).getByRole('option', { name: 'quote-v2' }));
  await user.click(screen.getByRole('button', { name: '下一步' }));
  await screen.findByText('权重步进 % *');
}

/** J5 — 金丝雀旅程：三步向导创建 / 前端校验拦截 / 权重手动回滚。 */
describe('J5 · 金丝雀旅程', () => {
  it('case1 创建 happy：三步向导 → POST canaryconfigs 201 → 列表出现新条目', async () => {
    // Given：两个 httptrigger + 两函数 + 可变 canaryconfigs 列表
    installCanaryHandlers();
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/canary']);
    await screen.findByText('金丝雀策略');

    // When：走完向导三步（步进 25 / 30s / 阈值 3 / failure）并确认生成
    await gotoStep3(user);
    await user.clear(within(fieldOf('权重步进 % *')).getByRole('spinbutton'));
    await user.type(within(fieldOf('权重步进 % *')).getByRole('spinbutton'), '25');
    await user.clear(within(fieldOf('步进间隔（秒）*')).getByRole('textbox'));
    await user.type(within(fieldOf('步进间隔（秒）*')).getByRole('textbox'), '30s');
    await user.click(screen.getByRole('button', { name: '确认生成' }));

    // Then：POST 请求体含七字段 spec；列表刷新后出现 quote-entry-canary 行
    expect(createdPostBodies).toHaveLength(1);
    expect(createdPostBodies[0]).toMatchObject({
      apiVersion: 'fission.io/v1', kind: 'CanaryConfig',
      metadata: { name: 'quote-entry-canary', namespace: 'fission' },
      spec: {
        trigger: 'quote-entry', oldfunction: 'quote', newfunction: 'quote-v2',
        weightincrement: 25, duration: '30s', failurethreshold: 3, failureType: 'failure',
      },
    });
    // 产品缺陷记录：CreateWizard POST 的是扁平包络 {name,namespace,spec}，而列表/后端读
    // metadata.name → 新条目名称列渲染 '—'，以触发器列 quote-entry 与计数 2 断言新条目出现。
    expect(await screen.findByText(/策略已提交/)).toBeInTheDocument();
    expect(await screen.findByText(/2 · 状态 5s 轮询/)).toBeInTheDocument();
    const tbody = (await screen.findAllByText('quote-rollout'))[0].closest('tbody') as HTMLElement;
    expect(within(tbody).getAllByRole('row')).toHaveLength(2);
    expect(within(tbody).getAllByText('quote-entry').length).toBeGreaterThan(0);
  });

  it('case2 weightincrement=150：前端拦截校验，不发 POST', async () => {
    // Given：同基线，向导已到第三步
    installCanaryHandlers();
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/canary']);
    await screen.findByText('金丝雀策略');
    await gotoStep3(user);

    // When：把权重步进改为 150 后点确认生成
    const inc = within(fieldOf('权重步进 % *')).getByRole('spinbutton');
    await user.clear(inc);
    await user.type(inc, '150');
    await user.click(screen.getByRole('button', { name: '确认生成' }));

    // Then：前端校验文案出现（toast），POST 未发出
    expect(await screen.findByText(/权重步进须在 1–100 之间/)).toBeInTheDocument();
    expect(createdPostBodies).toHaveLength(0);
  });

  it('case3 duration="abc"：校验文案提示 Go duration 格式', async () => {
    // Given：向导第三步，权重步进合法
    installCanaryHandlers();
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/canary']);
    await screen.findByText('金丝雀策略');
    await gotoStep3(user);

    // When：步进间隔清成 'abc'
    const dur = within(fieldOf('步进间隔（秒）*')).getByRole('textbox');
    await user.clear(dur);
    await user.type(dur, 'abc');
    await user.click(screen.getByRole('button', { name: '确认生成' }));

    // Then：Go duration 提示出现，POST 未发出
    expect(await screen.findByText(/必须是 Go duration 字符串/)).toBeInTheDocument();
    expect(createdPostBodies).toHaveLength(0);
  });

  it('case4 手动回滚：详情点「回滚 · 旧版 100%」→ PUT weights {quote:100,quote-v2:0} → toast', async () => {
    // Given：既有 quote-rollout 策略被默认选中，实时权重 70/30
    installCanaryHandlers();
    const user = userEvent.setup();
    const { renderApp } = await import('./helpers');
    renderApp(['/canary']);

    // When：点详情头部「回滚 · 旧版 100%」（quote-rollout 默认选中，名称在列表行与详情 h2 各出现一次）
    await user.click(await screen.findByRole('button', { name: '回滚 · 旧版 100%' }));

    // Then：PUT /httptriggers/fission/quote-http/weights 请求体由当前权重补齐 Σ=100
    await screen.findByText(/→ 100%，已写入 quote-http/);
    expect(weightPutBodies).toEqual([{ quote: 100, 'quote-v2': 0 }]);
  });
});
