/**
 * 批 1 · L2 状态机模拟：describe 加载状态序列（loading→loaded / error(501/403)）。
 *
 * SKIP-无显式状态机：当前实现为 useQuery 三态（web/src/api/hooks.ts useDescribe：
 * 501/404 → unavailable=true 降级占位；403 等其他错误不降级，data 为 null），
 * 无独立 reducer 可测。此处用与 useDescribe 语义一致的简单 reducer 表达状态序列，
 * 数据映射复用 normalizeDescribe 保证与真实渲染路径同源。
 */
import { describe, expect, it } from 'vitest';
import { normalizeDescribe } from '../pages/Functions';

type Phase = 'loading' | 'loaded' | 'unavailable' | 'error';

interface DescribeLoadState {
  phase: Phase;
  data: Record<string, unknown> | null;
}

/** 镜像 useDescribe 的状态归约（retry=false，一次请求一终态）。 */
function describeload(event: { ok: boolean; status?: number; data?: Record<string, unknown> }, _s: DescribeLoadState): DescribeLoadState {
  if (event.ok) return { phase: 'loaded', data: event.data ?? null };
  // useDescribe：仅 501（端点未上线）与 404 降级为 unavailable 占位
  if (event.status === 501 || event.status === 404) return { phase: 'unavailable', data: null };
  // 403（RBAC）等：不降级，保持 error（UI 显示「暂无 describe 数据」占位）
  return { phase: 'error', data: null };
}

const init: DescribeLoadState = { phase: 'loading', data: null };

describe('describeload · loading→loaded→数据映射', () => {
  it('成功响应进入 loaded 且经 normalizeDescribe 出字段', () => {
    const raw = { package: { buildStatus: 'succeeded' }, pods: { warm: 2, ready: 2 }, invocable: true, triggers: [] };
    const s = describeload({ ok: true, status: 200, data: raw }, init);
    expect(s.phase).toBe('loaded');
    const d = normalizeDescribe(s.data!);
    expect(d.buildLabel).toBe('READY');
    expect(d.warmPods).toBe(2);
    expect(d.invocable).toBe('Yes');
  });
});

describe('describeload · error 归类', () => {
  it('501 → unavailable（降级占位，UI 文案「describe 端点尚未上线（501）」）', () => {
    expect(describeload({ ok: false, status: 501 }, init).phase).toBe('unavailable');
  });
  it('403 → error（不降级；RBAC 问题需显式暴露）', () => {
    expect(describeload({ ok: false, status: 403 }, init).phase).toBe('error');
  });
  it('404 → unavailable（函数无 Pod/不存在时后端同样降级）', () => {
    expect(describeload({ ok: false, status: 404 }, init).phase).toBe('unavailable');
  });
});
