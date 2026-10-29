/**
 * Selfcheck · 审计项 A1（describe 契约）
 * 验证 normalizeDescribe 对三类真实 describe 响应样例的归一化：
 * buildStatus 三层兜底、warmPods、invocable 布尔→字符串、mcpExposed 缺失字段兜底。
 * Fixtures 由 server/routers/functions_ext.py describe_function() 的实际 return 转写。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { normalizeDescribe } from '../pages/Functions';

const fx = (n: string): Record<string, unknown> =>
  JSON.parse(readFileSync(fileURLToPath(new URL('./fixtures/' + n, import.meta.url)), 'utf8'));

describe('normalizeDescribe · 完整响应（poolmgr, 构建 succeeded）', () => {
  const d = normalizeDescribe(fx('describe.full.json'));
  it('buildStatus=succeeded → READY/ok', () => {
    expect(d.buildLabel).toBe('READY');
    expect(d.buildTone).toBe('ok');
  });
  it('warmPods 取自 pods.warm', () => expect(d.warmPods).toBe(3));
  it('invocable 布尔 true → "Yes"', () => expect(d.invocable).toBe('Yes'));
  it('mcpToolExposed.enabled → mcpExposed', () => expect(d.mcpExposed).toBe(true));
  it('triggers 透传', () => expect(d.triggers).toHaveLength(1));
});

describe('normalizeDescribe · package=null 兜底', () => {
  const d = normalizeDescribe(fx('describe.null-package.json'));
  it('buildStatus 兜底 none → 未构建/neutral', () => {
    expect(d.buildLabel).toBe('未构建');
    expect(d.buildTone).toBe('neutral');
  });
  it('warmPods=0 仍展示数字（不是 —）', () => expect(d.warmPods).toBe(0));
  it('invocable=true（poolmgr 冷启动）', () => expect(d.invocable).toBe('Yes'));
  it('mcpExposed=false', () => expect(d.mcpExposed).toBe(false));
});

describe('normalizeDescribe · 缺失 mcpToolExposed 字段', () => {
  const d = normalizeDescribe(fx('describe.no-mcp.json'));
  it('mcpExposed 兜底 undefined（UI 显示 —）', () => expect(d.mcpExposed).toBeUndefined());
  it('其余字段不受影响', () => {
    expect(d.buildLabel).toBe('READY');
    expect(d.warmPods).toBe(3);
  });
});

describe('normalizeDescribe · 内嵌 status.buildstatus 形态（fission CRD 原生包）', () => {
  it('package.status.buildstatus.status 三层展开', () => {
    const d = normalizeDescribe({ package: { status: { buildstatus: { status: 'failed' } } } });
    expect(d.buildLabel).toBe('ERROR');
    expect(d.buildTone).toBe('error');
  });
  it('package.status 直接是字符串', () => {
    const d = normalizeDescribe({ package: { status: 'pending' } });
    expect(d.buildLabel).toBe('PENDING');
    expect(d.buildTone).toBe('pending');
  });
  it('invocable 缺失 → No', () => {
    expect(normalizeDescribe({}).invocable).toBe('No');
  });
});
