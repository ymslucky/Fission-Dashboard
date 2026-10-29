/**
 * Selfcheck · 审计项 A2（列表响应归一化）
 * asList 对后端六种包装形态的统一取数组行为。
 */
import { describe, expect, it } from 'vitest';
import { asList } from '../api/client';

describe('asList', () => {
  it('{items:[]} → []', () => expect(asList({ items: [] })).toEqual([]));
  it('裸数组原样返回', () => {
    const arr = [{ name: 'a' }];
    expect(asList(arr)).toBe(arr);
  });
  it('{tools:[]} → 非默认 key 取值', () => {
    const arr = [{ name: 't' }];
    expect(asList({ tools: arr }, 'tools')).toBe(arr);
  });
  it('{functions:{map 形态}} → 非数组值返回 []（不伪装成列表）', () => {
    expect(asList({ functions: { hello: { cpu_m: 1 } } })).toEqual([]);
  });
  it('null → []', () => expect(asList(null)).toEqual([]));
  it('undefined → []', () => expect(asList(undefined)).toEqual([]));
  it('默认 key 是 items', () => {
    expect(asList({ items: [1, 2] })).toEqual([1, 2]);
  });
});
