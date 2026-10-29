/**
 * 函数日志连接状态机（纯函数，从 useFunctionLogs 的连接逻辑抽取）。
 *
 * 阶段：idle → connecting → streaming → (stream-end) reconnecting ≤ RETRY_LIMIT 次
 *       → connecting → … → streaming / done；404 → error(引导文案)；其他 !ok → error。
 *
 * 抽取说明（测试基础设施重构）：useFunctionLogs（web/src/api/hooks.ts）内部的
 * 重试上限 / 重订间隔 / 404 引导文案改为引用本模块常量，保证 hook 与状态机
 * 演进同源；hook 的 fetch/SSE 副作用仍留在 hook 内（浏览器流无法纯测）。
 */

export const RETRY_LIMIT = 5;
export const RETRY_DELAY_MS = 3000;

/** follow=true 且 pod 不存在时后端回 404，前端给出该引导文案（对齐 hooks.ts 原文案）。 */
export const NO_POD_GUIDANCE =
  '该函数当前没有运行中的专用 Pod（poolmgr 空闲属正常）。发起一次调用使其特化后，日志即可查看。';

export interface LogState {
  phase: 'idle' | 'connecting' | 'streaming' | 'reconnecting' | 'error' | 'done';
  connected: boolean;
  error: string | null;
  attempt: number;
}

export type LogEvent =
  | { type: 'start'; follow: boolean }
  | { type: 'response'; ok: boolean; status?: number }
  | { type: 'line' }
  | { type: 'stream-end' }
  | { type: 'network-error'; message?: string }
  | { type: 'retry-due' }
  | { type: 'abort' };

export const initialLogState: LogState = { phase: 'idle', connected: false, error: null, attempt: 0 };

/** 规约一条事件；不产生副作用，供 useFunctionLogs 与 L2 模拟测试共同驱动。 */
export function logStateMachine(event: LogEvent, state: LogState): LogState {
  switch (event.type) {
    case 'start':
      // 对齐 hooks 现状：发起请求即按 follow 预置 connected（乐观连接指示）
      return { phase: 'connecting', connected: event.follow, error: null, attempt: 0 };
    case 'response': {
      if (event.ok) return { ...state, phase: 'streaming', connected: true, error: null };
      if (event.status === 404) {
        return { ...state, phase: 'error', connected: false, error: NO_POD_GUIDANCE };
      }
      return {
        ...state,
        phase: 'error',
        connected: false,
        error: 'log request failed (' + (event.status ?? 0) + ')',
      };
    }
    case 'line':
      return { ...state, phase: 'streaming', connected: true };
    case 'stream-end':
      // 流正常结束（后端超时/断开）：≤ RETRY_LIMIT 次自动重订
      if (state.attempt < RETRY_LIMIT) {
        return { ...state, phase: 'reconnecting', connected: false };
      }
      return { ...state, phase: 'done', connected: false };
    case 'network-error':
      if (state.attempt < RETRY_LIMIT) {
        return { ...state, phase: 'reconnecting', connected: false };
      }
      return { ...state, phase: 'error', connected: false, error: event.message ?? 'network error' };
    case 'retry-due':
      return { ...state, phase: 'connecting', connected: true, attempt: state.attempt + 1 };
    case 'abort':
      return { ...state, phase: 'idle', connected: false };
  }
}
