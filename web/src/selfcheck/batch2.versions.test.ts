/**
 * 批 2 · L1 纯函数：版本时间/排序/别名指向（web/src/lib/versions.ts）。
 *
 * 重构说明（允许的测试基础设施重构）：fmtVersionTime / sortVersions / currentAliasOf
 * 原内联于 Versions.tsx（fmtTime、createdOf、useMemo 排序、v.name === aliasTarget），
 * 抽取为纯函数后页面引用 lib/versions.ts，行为不变。
 */
import { describe, expect, it } from 'vitest';
import {
  createdOf,
  currentAliasOf,
  fmtVersionTime,
  sortVersions,
  type AliasRef,
} from '../lib/versions';
import type { VersionInfo } from '../api/extend';

const v = (name: string, fields: Partial<VersionInfo> = {}): VersionInfo => ({
  name,
  ...fields,
});

describe('fmtVersionTime · created 优先 / creationTimestamp 兜底', () => {
  it('created 有值 → 按 created 格式化', () => {
    const out = fmtVersionTime('2026-01-02T03:04:05Z', '1999-01-01T00:00:00Z');
    expect(out).not.toBe('—');
    expect(out).toContain('2026');
  });
  it('created 缺失 → 回退 creationTimestamp', () => {
    const out = fmtVersionTime(undefined, '2026-01-02T03:04:05Z');
    expect(out).not.toBe('—');
    expect(out).toContain('2026');
  });
  it('两者都缺 → —', () => {
    expect(fmtVersionTime(undefined, undefined)).toBe('—');
    expect(fmtVersionTime('', '')).toBe('—');
  });
  it('非法时间串原样返回（不输出 Invalid Date）', () => {
    expect(fmtVersionTime('not-a-date')).toBe('not-a-date');
  });
  it('createdOf：created 优先，回退 creationTimestamp', () => {
    expect(createdOf(v('a', { created: 'c', creationTimestamp: 't' }))).toBe('c');
    expect(createdOf(v('a', { creationTimestamp: 't' }))).toBe('t');
    expect(createdOf(v('a'))).toBeUndefined();
  });
});

describe('sortVersions · creationTimestamp 降序', () => {
  it('新→旧排列；created 兜底参与比较', () => {
    const list = [
      v('old', { creationTimestamp: '2026-01-01T00:00:00Z' }),
      v('new', { creationTimestamp: '2026-01-03T00:00:00Z' }),
      v('mid', { creationTimestamp: '2026-01-02T00:00:00Z' }),
    ];
    expect(sortVersions(list).map((x) => x.name)).toEqual(['new', 'mid', 'old']);
    // 不改动入参
    expect(list[0].name).toBe('old');
  });
  it('时间键缺失排最后（空串比较），输入顺序稳定', () => {
    const list = [v('b', { created: '2026-01-01T00:00:00Z' }), v('a')];
    const out = sortVersions(list);
    expect(out.map((x) => x.name)).toEqual(['b', 'a']);
  });
});

describe('currentAliasOf · 别名指向高亮判定', () => {
  const aliases: AliasRef[] = [
    { name: 'prod', target: 'hello-v1' },
    { name: 'latest', target: 'hello-v2' },
  ];
  it('命中 → 返回指向该版本的别名', () => {
    expect(currentAliasOf(aliases, 'hello-v1')).toEqual({ name: 'prod', target: 'hello-v1' });
  });
  it('未命中 → undefined（不高亮）', () => {
    expect(currentAliasOf(aliases, 'hello-v3')).toBeUndefined();
    expect(currentAliasOf([], 'hello-v1')).toBeUndefined();
  });
});
