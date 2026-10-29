/**
 * 批 1 · L1 契约模拟：describe 契约（fixtures 从 server/routers/functions_ext.py
 * describe_function() 的实际 return 转写；triggers 元素固定为 {kind, ns, name, url}）。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { normalizeDescribe } from '../pages/Functions';

const fx = (n: string): Record<string, unknown> =>
  JSON.parse(readFileSync(fileURLToPath(new URL('./fixtures/' + n, import.meta.url)), 'utf8'));

describe('normalizeDescribe · batch1 fixture（newdeploy + canary + 加权触发器）', () => {
  const raw = fx('describe.batch1.weights.json');
  const d = normalizeDescribe(raw);

  it('triggers 元素保持服务端形状 {kind, ns, name, url}', () => {
    expect(d.triggers).toEqual([
      { kind: 'httptriggers', ns: 'ns1', name: 'canary-route', url: '/canary' },
      { kind: 'timetriggers', ns: 'ns1', name: 'tick', url: '' },
    ]);
  });
  it('httptrigger 加权形态（functionref 内嵌 functionweights 含该函数）出现在 triggers', () => {
    const http = d.triggers[0] as { kind: string; name: string };
    expect(http.kind).toBe('httptriggers');
    expect(http.name).toBe('canary-route'); // 根因回归：加权触发器此前被漏判
  });
  it('构建状态与包引用（package.buildStatus 顶层键）', () => {
    expect(d.buildLabel).toBe('READY');
    expect(d.buildTone).toBe('ok');
  });
  it('pods.warm=2（newdeploy 按函数 Pod 统计）', () => expect(d.warmPods).toBe(2));
  it('invocable 布尔 true → "Yes"', () => expect(d.invocable).toBe('Yes'));
  it('mcpExposed=false（注解未启用）', () => expect(d.mcpExposed).toBe(false));
});

describe('normalizeDescribe · 空 triggers → []', () => {
  it('无任何触发器', () => {
    const d = normalizeDescribe({ package: { buildStatus: 'succeeded' }, pods: { warm: 1, ready: 1 } });
    expect(d.triggers).toEqual([]);
  });
});
