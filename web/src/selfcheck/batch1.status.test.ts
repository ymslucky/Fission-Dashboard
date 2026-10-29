/**
 * 批 1 · L1 契约模拟（补充）：executorOf 三态。
 * spec.InvokeStrategy.ExecutionStrategy.ExecutorType 为准（对齐 functions_ext.py 第 46 行
 * 默认 poolmgr），注解兜底 fission.io/executor / executor，最后缺省 poolmgr。
 */
import { describe, expect, it } from 'vitest';
import { executorOf } from '../pages/Functions';

const fnWith = (spec: Record<string, unknown>, annotations?: Record<string, string>) =>
  ({ metadata: { name: 'f', namespace: 'ns1', annotations }, spec }) as never;

describe('executorOf · ExecutorType 三态', () => {
  it("newdeploy → 'newdeploy'", () =>
    expect(executorOf(fnWith({ InvokeStrategy: { ExecutionStrategy: { ExecutorType: 'newdeploy' } } }))).toBe('newdeploy'));
  it("poolmgr → 'poolmgr'", () =>
    expect(executorOf(fnWith({ InvokeStrategy: { ExecutionStrategy: { ExecutorType: 'poolmgr' } } }))).toBe('poolmgr'));
  it('缺失 → 注解兜底，再缺省 poolmgr', () => {
    expect(executorOf(fnWith({}, { 'fission.io/executor': 'newdeploy' }))).toBe('newdeploy');
    expect(executorOf(fnWith({}, { executor: 'poolmgr' }))).toBe('poolmgr');
    expect(executorOf(fnWith({}))).toBe('poolmgr');
  });
  it('ExecutorType 显式值优先于注解', () =>
    expect(executorOf(fnWith({ InvokeStrategy: { ExecutionStrategy: { ExecutorType: 'poolmgr' } } }, { executor: 'newdeploy' }))).toBe('poolmgr'));
});
