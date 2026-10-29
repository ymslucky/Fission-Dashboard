/**
 * Selfcheck · 审计项 A6（cron 契约）
 * parseCron/isValidCron 合法与非法形态；nextFireTimes 下次触发预览不抛异常。
 */
import { describe, expect, it } from 'vitest';
import { isValidCron, nextFireTimes, parseCron } from '../lib/cron';

const FROM = new Date(2026, 0, 1, 0, 0, 0, 0);

describe('isValidCron · 合法', () => {
  const ok = ['* * * * *', '*/5 * * * *', '0 3 * * 1', '0 0 1 1 *', '30 8 1,15 * *', '0 9-17 * * 1-5', '59 23 31 12 6'];
  for (const expr of ok) it(`"${expr}" 合法`, () => expect(isValidCron(expr)).toBe(true));
});

describe('isValidCron · 非法', () => {
  const bad = ['', '* * * *', '* * * * * *', '60 * * * *', '* 24 * * *', '* * 0 * *', '* * * 13 *', '* * * * 7', 'a * * * *', '*/0 * * * *', '5-1 * * * *'];
  for (const expr of bad) it(`"${expr}" 非法`, () => expect(isValidCron(expr)).toBe(false));
});

describe('parseCron 字段展开', () => {
  it('*/5 分钟 → 12 个值', () => expect(parseCron('*/5 * * * *').minute.values.size).toBe(12));
  it('1,15 → {1,15}', () => {
    const v = parseCron('0 0 1,15 * *').dom.values;
    expect(v.has(1)).toBe(true);
    expect(v.has(15)).toBe(true);
    expect(v.size).toBe(2);
  });
});

describe('nextFireTimes 预览不抛异常', () => {
  it('每分钟表达式 → 3 个连续分钟', () => {
    const t = nextFireTimes('* * * * *', 3, FROM);
    expect(t).toHaveLength(3);
    expect(t[0].getTime()).toBe(FROM.getTime() + 60_000);
    expect(t[2].getTime()).toBe(FROM.getTime() + 3 * 60_000);
  });
  it('每日 03:00 → 次日 03:00 起始', () => {
    const t = nextFireTimes('0 3 * * *', 2, FROM);
    expect(t[0].getHours()).toBe(3);
    expect(t[0].getMinutes()).toBe(0);
    expect(t[1].getDate()).toBe(t[0].getDate() + 1);
  });
  it('每周一 → 触发日均为周一', () => {
    for (const d of nextFireTimes('0 0 * * 1', 4, FROM)) expect(d.getDay()).toBe(1);
  });
  it('永远不触发的表达式（2月30日）在年限内不抛异常、返回空', () => {
    expect(() => nextFireTimes('0 0 30 2 *', 1, FROM)).not.toThrow();
    expect(nextFireTimes('0 0 30 2 *', 1, FROM)).toEqual([]);
  });
});
