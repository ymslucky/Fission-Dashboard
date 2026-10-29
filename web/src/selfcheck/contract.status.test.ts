/**
 * Selfcheck · 审计项 A7（状态色形映射快照）
 * toneForStatus 各状态 → 色形字的映射快照（色 + 形 + 文案冗余编码的 tone 部分）。
 */
import { describe, expect, it } from 'vitest';
import { toneForStatus, type StatusTone } from '../components/StatusBadge';

const SNAPSHOT: Record<string, StatusTone> = {
  // ok 组（含 Ready/TRUE 大小写归一）
  OK: 'ok', ready: 'ok', succeeded: 'ok', success: 'ok', running: 'ok',
  none: 'ok', built: 'ok', active: 'ok', true: 'ok', READY: 'ok',
  // pending 组
  pending: 'pending', build: 'pending', building: 'pending', waiting: 'pending',
  containercreating: 'pending', provisioning: 'pending', progressing: 'pending',
  // error 组
  failed: 'error', error: 'error', crashloopbackoff: 'error',
  imagepullbackoff: 'error', errimagepull: 'error', false: 'error', unavailable: 'error',
  // neutral 组
  unknown: 'neutral', '': 'neutral', weirdstate: 'neutral',
};

describe('toneForStatus 快照', () => {
  for (const [status, tone] of Object.entries(SNAPSHOT)) {
    it(`${JSON.stringify(status)} → ${tone}`, () => expect(toneForStatus(status)).toBe(tone));
  }
});
