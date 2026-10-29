/**
 * Selfcheck · 审计项 A4（metrics 归一化）
 * normalizeMetrics（从 useMetrics hook 导出的纯函数）对
 * map（后端实际形态，server/routers/observe.py #metrics）、数组（前向兼容）、空 三态。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { normalizeMetrics } from '../api/hooks';

const fx = (n: string): unknown =>
  JSON.parse(readFileSync(fileURLToPath(new URL('./fixtures/' + n, import.meta.url)), 'utf8'));

describe('normalizeMetrics · map 形态（后端实际返回）', () => {
  it('按函数名展开，cpu_m→cpuMilli, mem_mi→memoryMi', () => {
    const out = normalizeMetrics(fx('metrics.map.json'));
    expect(out).toHaveLength(2);
    expect(out.find((m) => m.name === 'hello')).toEqual({ name: 'hello', cpuMilli: 120, memoryMi: 256 });
    expect(out.find((m) => m.name === 'world')).toEqual({ name: 'world', cpuMilli: 0, memoryMi: 512 });
  });
});

describe('normalizeMetrics · 数组形态（前向兼容）', () => {
  it('取 name/cpu_m/mem_mi，缺省字段为 undefined', () => {
    expect(normalizeMetrics(fx('metrics.array.json'))).toEqual([
      { name: 'hello', cpuMilli: 120, memoryMi: 256 },
      { name: 'world', cpuMilli: undefined, memoryMi: undefined },
    ]);
  });
});

describe('normalizeMetrics · 空/异常', () => {
  it('{functions:{}} → []', () => expect(normalizeMetrics(fx('metrics.empty.json'))).toEqual([]));
  it('无 functions 键 → []', () => expect(normalizeMetrics({})).toEqual([]));
  it('functions=null → []', () => expect(normalizeMetrics({ functions: null })).toEqual([]));
});
