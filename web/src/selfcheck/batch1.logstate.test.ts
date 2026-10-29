/**
 * 批 1 · L2 状态机模拟：函数日志连接状态（logStateMachine，web/src/lib/logstate.ts）。
 *
 * 事件序列表（follow=true 主链）：
 *   start(follow) → connecting(connected=true, 乐观指示)
 *   response 200  → streaming
 *   line × N      → streaming（持续）
 *   stream-end    → reconnecting（attempt<5）
 *   retry-due     → connecting（attempt+1）
 *   response 200  → streaming → connected
 * 异常支：
 *   response 404  → error（NO_POD_GUIDANCE 引导文案，poolmgr 空闲属正常）
 *   response 500  → error（log request failed (500)）
 *   network-error × 6 → 前 5 次 reconnecting，第 6 次 error
 *   abort         → idle
 *
 * 重构说明：该状态机从 useFunctionLogs 的连接逻辑抽取为纯函数；
 * hooks.ts 现引用本模块的 RETRY_LIMIT / RETRY_DELAY_MS / NO_POD_GUIDANCE 常量，
 * fetch/SSE 副作用仍留在 hook 内（浏览器流无法纯测）。
 */
import { describe, expect, it } from 'vitest';
import {
  initialLogState,
  logStateMachine,
  NO_POD_GUIDANCE,
  RETRY_DELAY_MS,
  RETRY_LIMIT,
  type LogState,
} from '../lib/logstate';

const run = (events: Parameters<typeof logStateMachine>[0][], state: LogState = initialLogState): LogState =>
  events.reduce<LogState>((s, e) => logStateMachine(e, s), state);

describe('logStateMachine · 主链 connecting→streaming→reconnecting→connected', () => {
  it('stream-end 后重订 ≤ RETRY_LIMIT 次后回到 connected', () => {
    let s = run([
      { type: 'start', follow: true },
      { type: 'response', ok: true, status: 200 },
      { type: 'line' },
    ]);
    expect(s.phase).toBe('streaming');
    expect(s.connected).toBe(true);
    // 断流 → 重订 5 次（attempt 0..4 都允许），第 5 次重订后再次断流 → done
    for (let i = 0; i < RETRY_LIMIT; i++) {
      s = run([{ type: 'stream-end' }], s);
      expect(s.phase).toBe('reconnecting');
      expect(s.connected).toBe(false);
      s = run([{ type: 'retry-due' }], s);
      expect(s.attempt).toBe(i + 1);
      expect(s.phase).toBe('connecting');
      s = run([{ type: 'response', ok: true, status: 200 }], s);
      expect(s.phase).toBe('streaming');
    }
    s = run([{ type: 'stream-end' }], s);
    expect(s.phase).toBe('done');
    expect(s.error).toBeNull();
  });
});

describe('logStateMachine · 404 引导（回归）', () => {
  it('response 404 → error + NO_POD_GUIDANCE（不重试）', () => {
    const s = run([{ type: 'start', follow: true }, { type: 'response', ok: false, status: 404 }]);
    expect(s.phase).toBe('error');
    expect(s.connected).toBe(false);
    expect(s.error).toBe(NO_POD_GUIDANCE);
  });
  it('其他非 ok 状态 → 通用错误文案', () => {
    const s = run([{ type: 'start', follow: false }, { type: 'response', ok: false, status: 500 }]);
    expect(s.error).toBe('log request failed (500)');
  });
});

describe('logStateMachine · 网络错误重试与中止', () => {
  it('network-error 前 RETRY_LIMIT 次 reconnecting，超出后 error', () => {
    let s = run([{ type: 'start', follow: true }]);
    for (let i = 0; i < RETRY_LIMIT; i++) {
      s = run([{ type: 'network-error' }], s);
      expect(s.phase).toBe('reconnecting');
      s = run([{ type: 'retry-due' }], s);
    }
    s = run([{ type: 'network-error', message: 'boom' }], s);
    expect(s.phase).toBe('error');
    expect(s.error).toBe('boom');
  });
  it('abort → idle', () => {
    const s = run([{ type: 'start', follow: true }, { type: 'response', ok: true, status: 200 }, { type: 'abort' }]);
    expect(s.phase).toBe('idle');
    expect(s.connected).toBe(false);
  });
  it('常量契约：RETRY_LIMIT=5、RETRY_DELAY_MS=3000（对齐 hooks 原实现）', () => {
    expect(RETRY_LIMIT).toBe(5);
    expect(RETRY_DELAY_MS).toBe(3000);
  });
});
