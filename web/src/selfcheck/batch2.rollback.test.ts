/**
 * 批 2 · L2 回滚确认流：rollbackGate（web/src/lib/versions.ts）。
 *
 * SKIP-无显式状态机：现有实现为 ConfirmDialog(输版本号) → POST → toast，
 * 无状态机对象。此处以「事件序列：输入 → gate → (模拟 POST 成功) → toast 文案」
 * 的函数序列表测纯逻辑；async POST 副作用（useRollback mutation）不在纯测范围。
 */
import { describe, expect, it } from 'vitest';
import {
  COLD_START_WARNING,
  rollbackGate,
  type AliasRef,
} from '../lib/versions';
import type { VersionInfo } from '../api/extend';

const versions: VersionInfo[] = [
  { name: 'hello-v2', created: '2026-01-03T00:00:00Z', functionExists: true },
  { name: 'hello-v1', created: '2026-01-01T00:00:00Z', functionExists: false },
];
const prod: AliasRef = { name: 'prod', target: 'hello-v1' };

describe('rollbackGate · 拒绝分支', () => {
  it('版本号不在列表 → 拒绝(版本不存在)', () => {
    const g = rollbackGate('hello-v9', versions, prod);
    expect(g).toEqual({ ok: false, reason: '版本不存在' });
  });
  it('等于当前别名指向 → 拒绝(已指向该版本)', () => {
    const g = rollbackGate('hello-v1', versions, prod);
    expect(g.ok).toBe(false);
    expect(g.ok ? '' : g.reason).toBe('已指向该版本');
  });
});

describe('rollbackGate · 放行分支', () => {
  it('合法目标 → {toVersion, coldStartWarning 必附}', () => {
    const g = rollbackGate('hello-v2', versions, prod);
    expect(g).toEqual({ ok: true, toVersion: 'hello-v2', coldStartWarning: COLD_START_WARNING });
  });
  it('无别名（alias=null）→ 只查版本存在性', () => {
    const g = rollbackGate('hello-v1', versions, null);
    expect(g.ok).toBe(true);
  });
});

describe('事件序列：输入 → gate → POST(模拟成功) → toast', () => {
  // 模拟 Versions.tsx doRollback 链路：gate 通过后 POST 返回 coldStartWarning → toast 文案
  const fakePost = async (body: { alias: string; toVersion: string }) => ({
    ok: true,
    alias: body.alias,
    version: body.toVersion,
    coldStartWarning: COLD_START_WARNING,
  });

  it('gate 通过 → POST → toast 含 coldStartWarning', async () => {
    const input = 'hello-v2';
    const gate = rollbackGate(input, versions, prod);
    expect(gate.ok).toBe(true);
    if (!gate.ok) return;
    const res = await fakePost({ alias: prod.name, toVersion: gate.toVersion });
    const toast = '回滚 hello' + ' · ' + (res.coldStartWarning ?? '已回滚到 ' + input);
    expect(toast).toContain(COLD_START_WARNING);
    expect(gate.coldStartWarning).toBe(res.coldStartWarning);
  });

  it('gate 拒绝 → 不发 POST（短路）', async () => {
    const gate = rollbackGate('hello-v1', versions, prod);
    if (gate.ok) throw new Error('should not pass');
    expect(gate.reason).toBe('已指向该版本');
  });
});
