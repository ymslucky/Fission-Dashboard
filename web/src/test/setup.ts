import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from './msw';

// jsdom 没有 EventSource；产品代码 useWatch 会在壳层订阅 /api/watch/*。
// 这里提供最小 stub（不产生事件，等价于 watch 未连接），不 stub 任何产品组件。
class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readyState = 2;
  onopen: unknown = null;
  onerror: unknown = null;
  onmessage: unknown = null;
  constructor(_url?: string | URL) { void _url; }
  addEventListener(): void { /* no-op */ }
  removeEventListener(): void { /* no-op */ }
  close(): void { /* no-op */ }
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).EventSource = FakeEventSource;

// jsdom 的 AbortSignal 与 Node24 内置 undici 的 Request 不互认：
// react-router 数据路由导航（createClientSideRequest）与 msw 拦截器都会
// new Request(url, { signal: jsdomSignal })，触发
// "Expected signal (AbortSignal {}) to be an instance of AbortSignal"。
// 包一层容错 Request：遇到 signal 互斥即剥离 signal 重试（旅程测试不依赖真实中止语义）。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const NativeRequest = globalThis.Request as any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
class TolerantRequest extends NativeRequest {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  constructor(input?: any, init?: any) {
    try {
      super(input, init);
    } catch (e) {
      if (e instanceof TypeError && String((e as Error).message).includes('AbortSignal')) {
        super(input, { ...init, signal: undefined });
      } else {
        throw e;
      }
    }
  }
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
// eslint-disable-next-line @typescript-eslint/no-explicit-any
globalThis.Request = TolerantRequest as any;

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
  // msw 补丁之上再包一层 fetch，剥掉 jsdom signal（同上原因）
  const patched = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    patched(input, { ...init, signal: undefined })) as typeof fetch;
});

afterEach(() => server.resetHandlers());
afterAll(() => server.close());
