/**
 * Selfcheck · 审计项 A3（错误契约）
 * getErrMessage 对后端各错误形态取人话消息；errStatus 识别 501/403/404 降级码。
 */
import { describe, expect, it } from 'vitest';
import { getErrMessage, errStatus } from '../lib/err';
import { ApiError } from '../api/client';

describe('getErrMessage', () => {
  it('{"detail":{"message":"x"}} → x', () =>
    expect(getErrMessage({ detail: { message: 'x' } })).toBe('x'));
  it('{"detail":"y"} → y', () =>
    expect(getErrMessage({ detail: 'y' })).toBe('y'));
  it('{"message":"z"} → z', () =>
    expect(getErrMessage({ message: 'z' })).toBe('z'));
  it('纯文本原样', () => expect(getErrMessage('boom')).toBe('boom'));
  it('非 JSON 对象 → JSON 字符串化，绝不空白', () => {
    const out = getErrMessage({ weird: true });
    expect(out.length).toBeGreaterThan(0);
    expect(out).toContain('weird');
  });
  it('Error 实例取 message', () =>
    expect(getErrMessage(new Error('inner'))).toBe('inner'));
  it('数字 → String 化', () => expect(getErrMessage(42)).toBe('42'));
});

describe('errStatus', () => {
  it('501 识别（未配置的端点）', () =>
    expect(errStatus(new ApiError('not implemented', 501))).toBe(501));
  it('403 识别（RBAC 拒绝）', () =>
    expect(errStatus(new ApiError('forbidden', 403))).toBe(403));
  it('404 识别', () =>
    expect(errStatus(new ApiError('nf', 404))).toBe(404));
  it('非 ApiError → null', () => expect(errStatus(new Error('x'))).toBeNull());
  it('status 非 number → null', () =>
    expect(errStatus({ status: '501' })).toBeNull());
});
