/**
 * Selfcheck · 审计项 A5（canary 提交体契约）
 * 前端提交体字段名与后端 CANARY_REQUIRED（server/routers/canary.py）一一对应；
 * weights Σ=100 校验（后端 PUT /httptriggers/{ns}/{name}/weights 强制总和 100）。
 * 另记录两处实测发现的字段形状差异（本测试固定其现状，供审计对照）。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { CANARY_FIELDS, CANARY_FAILURE_TYPES, weightsSumTo100, validateCanaryBody } from '../lib/canary';

const fx = (n: string): unknown =>
  JSON.parse(readFileSync(fileURLToPath(new URL('./fixtures/' + n, import.meta.url)), 'utf8'));

describe('canary 提交体字段名', () => {
  it('与后端 CANARY_REQUIRED 七字段一致', () => {
    expect([...CANARY_FIELDS].sort()).toEqual(
      ['duration', 'failureType', 'failurethreshold', 'newfunction', 'oldfunction', 'trigger', 'weightincrement'].sort(),
    );
  });
  it('失败类型仅 failure/timeout', () =>
    expect([...CANARY_FAILURE_TYPES].sort()).toEqual(['failure', 'timeout']));
  it('向导产出的提交体通过 validateCanaryBody', () => {
    const body = {
      name: 'hello-get-canary', namespace: 'default', trigger: 'hello-get',
      oldfunction: 'hello-v1', newfunction: 'hello-v2',
      weightincrement: 10, duration: '2m', failurethreshold: 3, failureType: 'failure',
    };
    expect(validateCanaryBody(body)).toBeNull();
  });
  it('缺任一必填字段时定位到该字段', () => {
    const body: Record<string, unknown> = {
      newfunction: 'a', oldfunction: 'b', trigger: 'c',
      weightincrement: 10, duration: '2m', failurethreshold: 3, failureType: 'failure',
    };
    delete body.trigger;
    expect(validateCanaryBody(body)).toBe('trigger');
  });
  it('weightincrement 越界（>100 / 0）被拒', () => {
    const base = { newfunction: 'a', oldfunction: 'b', trigger: 'c', duration: '2m', failurethreshold: 3, failureType: 'failure' };
    expect(validateCanaryBody({ ...base, weightincrement: 101 })).toBe('weightincrement');
    expect(validateCanaryBody({ ...base, weightincrement: 0 })).toBe('weightincrement');
  });
});

describe('weights Σ=100 校验', () => {
  it('{old:90,new:10} 通过', () => expect(weightsSumTo100({ 'hello-v1': 90, 'hello-v2': 10 })).toBe(true));
  it('{old:100} 通过（回滚干预）', () => expect(weightsSumTo100({ 'hello-v1': 100 })).toBe(true));
  it('总和 99 拒绝', () => expect(weightsSumTo100({ a: 50, b: 49 })).toBe(false));
  it('空对象拒绝', () => expect(weightsSumTo100({})).toBe(false));
  it('负数 / 非整数 / >100 拒绝', () => {
    expect(weightsSumTo100({ a: -10, b: 110 })).toBe(false);
    expect(weightsSumTo100({ a: 50.5, b: 49.5 })).toBe(false);
  });
});

describe('前端消费契约（已按 BE 实际形态修复）', () => {
  it('Canary 列表消费 c.spec.*（CRD spec 嵌套，而非顶层字段）', () => {
    const cfg = (fx('canary.configs.json') as { items: Record<string, unknown>[] }).items[0];
    expect(cfg.newfunction).toBeUndefined();
    const spec = cfg.spec as Record<string, unknown>;
    expect(spec.newfunction).toBe('hello-v2');
    expect(spec.oldfunction).toBe('hello-v1');
    expect(spec.trigger).toBe('hello-get');
    expect(spec.weightincrement).toBe(10);
    expect(spec.duration).toBe(300);
    expect(spec.failurethreshold).toBe(3);
    expect(spec.failureType).toBe('failure');
  });
  it('canary status 读 stage（阶段字符串）与 functionWeights', () => {
    const st = fx('canary.status.json') as Record<string, unknown>;
    expect(st.weights).toBeUndefined();
    expect(st.stage).toBe('Running');
    expect(st.functionWeights).toEqual({ 'hello-v1': 90, 'hello-v2': 10 });
    expect(st.failures).toBe(0);
  });
  it('干预权重体：当前 functionWeights 补齐 Σ=100（目标 100，其余 0）', () => {
    const w = { 'hello-v1': 90, 'hello-v2': 10 };
    const build = (target: string): Record<string, number> => {
      const body: Record<string, number> = {};
      for (const k of Object.keys(w)) body[k] = 0;
      body[target] = 100;
      return body;
    };
    expect(build('hello-v1')).toEqual({ 'hello-v1': 100, 'hello-v2': 0 });
    expect(weightsSumTo100(build('hello-v2'))).toBe(true);
  });
});
