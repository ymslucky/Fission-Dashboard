import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { givenOnQuoteDetail, mockTestEndpoint } from './helpers';

/** 读 TestConsole.tsx：归因卡片是 .obs-card，内含 StatusBadge/latency/requestId/component/reason 行。 */
async function outcomeCard(): Promise<HTMLElement> {
  const heading = await screen.findByText('归因卡片');
  return heading.closest('.obs-card') as HTMLElement;
}

/** 进入调用测试页签并点击表单里的「发起调用」。 */
async function invoke(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole('button', { name: '发起调用' })); // Inspector 头部按钮 → 切到 test 页签
  const runButtons = await screen.findAllByRole('button', { name: '发起调用' });
  await user.click(runButtons[runButtons.length - 1]); // TestConsole 表单按钮
}

/**
 * J2 — 调用测试旅程：从总览出发，真实跨路由进入函数详情，
 * 打开「调用测试」页签，发起调用并读取归因卡片。
 */
describe('J2 · 函数调用测试旅程', () => {
  it('case1 成功调用：归因卡片显示 200 / function / ok / rid-1', async () => {
    // Given：基线 handlers（test 端点 200），用户已进入 quote 详情
    const user = userEvent.setup();
    await givenOnQuoteDetail(user);

    // When：进入调用测试页签并发起调用
    await invoke(user);

    // Then：归因卡片出现，只断言用户可见 DOM
    const card = await outcomeCard();
    expect(card).toBeInTheDocument();
    expect(within(card).getByText('200')).toBeInTheDocument();
    expect(within(card).getByText('function')).toBeInTheDocument();
    expect(within(card).getByText('ok')).toBeInTheDocument();
    expect(within(card).getByText('rid-1')).toBeInTheDocument();
    expect(within(card).getByText('59 ms')).toBeInTheDocument();
  });

  it('case2 404：归因卡片显示 router 组件与 function or route not found', async () => {
    // Given：test 端点被覆盖为 404 变体（router 组件归因）
    mockTestEndpoint({
      status: 404, latencyMs: 8, requestId: '', component: 'router',
      reason: 'function or route not found', body: '', headers: {},
    });
    const user = userEvent.setup();
    await givenOnQuoteDetail(user);

    // When：发起调用
    await invoke(user);

    // Then：归因卡片显示 404 / router / not found
    const card = await outcomeCard();
    expect(within(card).getByText('404')).toBeInTheDocument();
    expect(within(card).getByText('router')).toBeInTheDocument();
    expect(within(card).getByText('function or route not found')).toBeInTheDocument();
  });

  it('case3 超时：status=null 时卡片显示超时原因与 60000 ms 延迟', async () => {
    // Given：test 端点返回 status:null（fission 60s 超时）
    mockTestEndpoint({
      status: null, latencyMs: 60000, requestId: '', component: 'router',
      reason: 'fission function timeout (60s，Fission 默认 60s)', body: '', headers: {},
    });
    const user = userEvent.setup();
    await givenOnQuoteDetail(user);

    // When：发起调用
    await invoke(user);

    // Then：修复后 status=null 渲染 UNKNOWN 徽章（组件缺陷已修）；
    // reason 与 latency 仍是用户可见的超时信号。
    const card = await outcomeCard();
    expect(within(card).getByText('fission function timeout (60s，Fission 默认 60s)')).toBeInTheDocument();
    expect(within(card).getByText('60000 ms')).toBeInTheDocument();
    expect(within(card).getByText('UNKNOWN')).toBeInTheDocument();
  });
});
