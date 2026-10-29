/**
 * 批 1 · L1 契约模拟：triggerMatches 四形态 + normalizeDescribe 三变体。
 * 被测：web/src/pages/Functions.tsx（triggerMatches / normalizeDescribe）。
 */
import { describe, expect, it } from 'vitest';
import { normalizeDescribe, triggerMatches } from '../pages/Functions';

describe('triggerMatches · functionref.name 单函数命中', () => {
  const t = { spec: { functionref: { type: 'name', name: 'hello' }, relativeurl: '/hello' } };
  it('命中同名函数', () => expect(triggerMatches(t, 'hello')).toBe(true));
  it('不命中其他函数', () => expect(triggerMatches(t, 'other')).toBe(false));
});

describe('triggerMatches · function-weights 内嵌 functionweights 命中', () => {
  // v1.27 加权形态：functionweights 嵌在 functionref 对象内部（与 functions_ext.py 对齐）
  const t = { spec: { functionref: { type: 'function-weights', name: '', functionweights: { hello: 80, world: 20 } } } };
  it('权重表内函数均命中', () => {
    expect(triggerMatches(t, 'hello')).toBe(true);
    expect(triggerMatches(t, 'world')).toBe(true);
  });
  it('权重表外不命中', () => expect(triggerMatches(t, 'other')).toBe(false));
  it('兼容 spec 顶层 functionweights 旧形态', () =>
    expect(triggerMatches({ spec: { functionweights: { hello: 100 } } }, 'hello')).toBe(true));
});

describe('triggerMatches · name 为空串且无 weights 不命中', () => {
  const t = { spec: { functionref: { type: 'function-weights', name: '' } } };
  it('任意函数名均不命中', () => {
    expect(triggerMatches(t, 'hello')).toBe(false);
    expect(triggerMatches(t, '')).toBe(false);
  });
});

describe('triggerMatches · labels 兜底缺失不崩溃', () => {
  it('spec 无 functionref（labels 由服务端兜底）→ false 且不抛', () => {
    expect(() => triggerMatches({ spec: {} }, 'hello')).not.toThrow();
    expect(triggerMatches({ spec: {} }, 'hello')).toBe(false);
  });
  it('functionref 为 null → false 且不抛', () => {
    expect(() => triggerMatches({ spec: { functionref: null } }, 'hello')).not.toThrow();
    expect(triggerMatches({ spec: { functionref: null } }, 'hello')).toBe(false);
  });
});

describe('normalizeDescribe · 三变体', () => {
  it('缺 package → buildStatus 兜底 none → 未构建/neutral', () => {
    const d = normalizeDescribe({ pods: { warm: 0, ready: 0 } });
    expect(d.buildLabel).toBe('未构建');
    expect(d.buildTone).toBe('neutral');
    expect(d.triggers).toEqual([]);
  });
  it("package.buildStatus='failed' → ERROR/error", () => {
    const d = normalizeDescribe({ package: { name: 'p1', buildStatus: 'failed', buildlog: 'boom', object: null } });
    expect(d.buildLabel).toBe('ERROR');
    expect(d.buildTone).toBe('error');
  });
  it('pods 缺 warm → warmPods undefined（UI 显示 —）', () => {
    const d = normalizeDescribe({ package: { buildStatus: 'succeeded' }, pods: { ready: 2 } });
    expect(d.warmPods).toBeUndefined();
  });
});
