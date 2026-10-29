import { useEffect, useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
} from '@tanstack/react-query';
import { api, ApiError, sseUrl } from './client';
import { NO_POD_GUIDANCE, RETRY_DELAY_MS, RETRY_LIMIT } from '../lib/logstate';
import { asList } from './client';
import { kindPath } from './kinds';
import type {
  FissionResource,
  Overview,
  ResourceKind,
  DescribeInfo,
  PodInfo,
  FunctionEvent,
  TestRequest,
  TestResult,
} from './types';

export const queryKeys = {
  list: (kind: ResourceKind) => ['list', kind] as const,
  resource: (kind: ResourceKind, ns: string, name: string) => ['resource', kind, ns, name] as const,
  overview: ['overview'] as const,
  metrics: ['metrics'] as const,
  describe: (ns: string, name: string) => ['describe', ns, name] as const,
  pods: (ns: string, name: string) => ['pods', ns, name] as const,
  events: (ns: string, name: string) => ['events', ns, name] as const,
};

/** GET /api/{kind} (optionally namespaced) */
export function useList<K extends FissionResource>(kind: ResourceKind, namespace?: string) {
  return useQuery<K[]>({
    queryKey: [...queryKeys.list(kind), namespace ?? ''],
    queryFn: async () => asList<K>(await api.get<unknown>(kindPath(kind, namespace))),
  });
}

/** GET /api/{kind}/{ns}/{name} */
export function useResource<K extends FissionResource>(kind: ResourceKind, ns: string, name: string) {
  return useQuery<K>({
    queryKey: queryKeys.resource(kind, ns, name),
    queryFn: () => api.get<K>(kindPath(kind, ns, name)),
    enabled: Boolean(ns && name),
  });
}

export interface MutationHooks<K extends FissionResource> {
  create: UseMutationResult<K, Error, { ns: string; body: K }>;
  update: UseMutationResult<K, Error, { ns: string; name: string; body: K }>;
  remove: UseMutationResult<unknown, Error, { ns: string; name: string }>;
}

/** create/update/delete mutations that invalidate the kind's list+detail queries */
export function useResourceMutations<K extends FissionResource>(kind: ResourceKind): MutationHooks<K> {
  const qc = useQueryClient();
  const invalidate = async () => {
    await qc.invalidateQueries({ queryKey: ['list', kind] });
    await qc.invalidateQueries({ queryKey: ['resource', kind] });
  };

  const create = useMutation<K, Error, { ns: string; body: K }>({
    mutationFn: ({ ns, body }) => api.post<K>(kindPath(kind, ns), body),
    onSuccess: invalidate,
  });

  const update = useMutation<K, Error, { ns: string; name: string; body: K }>({
    mutationFn: ({ ns, name, body }) => api.put<K>(kindPath(kind, ns, name), body),
    onSuccess: async (_data, vars) => {
      await invalidate();
      await qc.invalidateQueries({ queryKey: queryKeys.resource(kind, vars.ns, vars.name) });
    },
  });

  const remove = useMutation<unknown, Error, { ns: string; name: string }>({
    mutationFn: ({ ns, name }) => api.delete(kindPath(kind, ns, name)),
    onSuccess: invalidate,
  });

  return { create, update, remove };
}

/** GET /api/overview */
export function useOverview() {
  return useQuery<Overview>({
    queryKey: queryKeys.overview,
    queryFn: () => api.get<Overview>('/overview'),
    refetchInterval: 15_000,
  });
}

/** GET /api/metrics — 501 when PROMETHEUS_URL is not configured */
export interface FunctionMetric {
  name: string;
  cpuMilli?: number;
  memoryMi?: number;
}

export interface MetricsResult {
  functions: FunctionMetric[] | null;
  /** backwards-compatible alias */
  points: FunctionMetric[] | null;
  unavailable: boolean;
}

/** Backend answers {functions:{name:{cpu_m,mem_mi}}} (map) or {functions:[...]}; normalize to a list. */
function normalizeMetrics(raw: unknown): FunctionMetric[] {
  const fns = (raw as { functions?: unknown })?.functions;
  if (Array.isArray(fns)) {
    return fns
      .filter((m): m is Record<string, unknown> => typeof m === 'object' && m !== null)
      .map((m) => ({
        name: String(m.name ?? '?'),
        cpuMilli: typeof (m.cpu_m ?? m.cpuMilli) === 'number' ? (m.cpu_m ?? m.cpuMilli) as number : undefined,
        memoryMi: typeof (m.mem_mi ?? m.memoryMi) === 'number' ? (m.mem_mi ?? m.memoryMi) as number : undefined,
      }));
  }
  if (fns && typeof fns === 'object') {
    return Object.entries(fns as Record<string, Record<string, unknown>>).map(([name, v]) => ({
      name,
      cpuMilli: typeof v.cpu_m === 'number' ? v.cpu_m : undefined,
      memoryMi: typeof v.mem_mi === 'number' ? v.mem_mi : undefined,
    }));
  }
  return [];
}

export function useMetrics(): MetricsResult {
  const q = useQuery<FunctionMetric[]>({
    queryKey: queryKeys.metrics,
    queryFn: async () => normalizeMetrics(await api.get<unknown>('/metrics')),
    refetchInterval: 15_000,
    retry: false,
  });
  if (q.error instanceof ApiError && (q.error.status === 501 || q.error.status === 404)) {
    return { functions: null, points: null, unavailable: true };
  }
  return { functions: q.data ?? null, points: q.data ?? null, unavailable: false };
}

export interface DescribeResult {
  data: DescribeInfo | null;
  unavailable: boolean;
}

/**
 * GET /api/functions/{ns}/{name}/describe — backend in progress; degrade to a
 * placeholder when the endpoint answers 501/404 instead of blocking the UI.
 */
export function useDescribe(ns: string, name: string): DescribeResult {
  const q = useQuery<DescribeInfo>({
    queryKey: queryKeys.describe(ns, name),
    queryFn: () => api.get<DescribeInfo>('/functions/' + encodeURIComponent(ns) + '/' + encodeURIComponent(name) + '/describe'),
    enabled: Boolean(ns && name),
    retry: false,
    staleTime: 10_000,
  });
  if (q.error instanceof ApiError && (q.error.status === 501 || q.error.status === 404)) {
    return { data: null, unavailable: true };
  }
  return { data: q.data ?? null, unavailable: false };
}

export interface PodsResult {
  data: PodInfo[] | null;
  unavailable: boolean;
}

/** GET /api/functions/{ns}/{name}/pods */
export function useFunctionPods(ns: string, name: string): PodsResult {
  const q = useQuery<PodInfo[]>({
    queryKey: queryKeys.pods(ns, name),
    queryFn: async () => asList<PodInfo>(await api.get<unknown>('/functions/' + encodeURIComponent(ns) + '/' + encodeURIComponent(name) + '/pods')),
    enabled: Boolean(ns && name),
    retry: false,
  });
  if (q.error instanceof ApiError && (q.error.status === 501 || q.error.status === 404)) {
    return { data: null, unavailable: true };
  }
  return { data: q.data ?? null, unavailable: false };
}

export interface EventsResult {
  data: FunctionEvent[] | null;
  unavailable: boolean;
}

/** GET /api/functions/{ns}/{name}/events */
export function useFunctionEvents(ns: string, name: string): EventsResult {
  const q = useQuery<FunctionEvent[]>({
    queryKey: queryKeys.events(ns, name),
    queryFn: async () => asList<FunctionEvent>(await api.get<unknown>('/functions/' + encodeURIComponent(ns) + '/' + encodeURIComponent(name) + '/events')),
    enabled: Boolean(ns && name),
    retry: false,
  });
  if (q.error instanceof ApiError && (q.error.status === 501 || q.error.status === 404)) {
    return { data: null, unavailable: true };
  }
  return { data: q.data ?? null, unavailable: false };
}

/** POST /api/functions/{ns}/{name}/test */
export function useFunctionTest(ns: string, name: string) {
  return useMutation<TestResult, Error, TestRequest>({
    mutationFn: (req) =>
      api.post<TestResult>('/functions/' + encodeURIComponent(ns) + '/' + encodeURIComponent(name) + '/test', req),
  });
}

/** GET /api/functions/{ns}/{name}/versions — hook prepared, degrades to placeholder */
export function useFunctionVersions(ns: string, name: string) {
  return useQuery<unknown[]>({
    queryKey: ['versions', ns, name],
    queryFn: () => api.get<unknown[]>('/functions/' + encodeURIComponent(ns) + '/' + encodeURIComponent(name) + '/versions'),
    enabled: Boolean(ns && name),
    retry: false,
  });
}

export interface LogStream {
  lines: string[];
  connected: boolean;
  error: string | null;
}

function baseName(): string {
  return '/functions';
}

/**
 * Function logs: GET /api/functions/{ns}/{name}/logs?pod=&follow=
 * follow=true  → SSE 流，按 "data: " 前缀解析 JSON {pod,line}，渲染 "[pod] line"，
 *                解析失败的行保留原样；流结束后 3s 自动重订（最多 5 次）。
 * follow=false → 一次性响应 {pods,logs:{pod:text}}，按 pod 输出文本块（pod 名做分隔头）。
 */
export function useFunctionLogs(ns: string, name: string, pod: string | undefined, follow: boolean): LogStream {
  const [lines, setLines] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ns || !name) return;
    const ctrl = new AbortController();
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    setLines([]);
    setError(null);
    setConnected(follow);

    const qs = new URLSearchParams();
    if (pod) qs.set('pod', pod);
    if (follow) qs.set('follow', 'true');
    const url = sseUrl(baseName() + '/' + encodeURIComponent(ns) + '/' + encodeURIComponent(name) + '/logs', Object.fromEntries(qs));

    /** 把一条 SSE data 载荷渲染成日志行：能解析 {pod,line} 则加 pod 前缀，否则保留原样。 */
    const renderData = (payload: string): string => {
      try {
        const obj = JSON.parse(payload) as { pod?: string; line?: string; error?: string };
        if (typeof obj.error === 'string') return obj.error;
        if (typeof obj.line === 'string') return obj.pod ? '[' + obj.pod + '] ' + obj.line : obj.line;
      } catch { /* 非 JSON 行保留原样 */ }
      return payload;
    };

    const finish = (msg: string | null): void => {
      setConnected(false);
      if (msg) setError(msg);
    };

    const run = async (attempt: number): Promise<void> => {
      try {
        const res = await fetch(url, { signal: ctrl.signal });
        if (res.status === 404) {
          finish(NO_POD_GUIDANCE);
          return;
        }
        if (!res.ok) {
          let msg = 'log request failed (' + res.status + ')';
          try {
            const data = (await res.json()) as { message?: string };
            if (data.message) msg = data.message;
          } catch { /* keep default */ }
          finish(msg);
          return;
        }
        if (follow) {
          // SSE 流：逐行消费，"data: " 前缀解析为 {pod,line}
          const reader = res.body!.getReader();
          const decoder = new TextDecoder();
          let buf = '';
          const collected: string[] = [];
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const parts = buf.split('\n');
            buf = parts.pop() ?? '';
            for (const raw of parts) {
              const line = raw.replace(/\r$/, '');
              if (line.startsWith('data: ')) collected.push(renderData(line.slice(6)));
              else if (line.startsWith('data:')) collected.push(renderData(line.slice(5).trimStart()));
            }
            if (collected.length) setLines((old) => old.concat(collected.splice(0)));
          }
          if (buf.startsWith('data:')) collected.push(renderData(buf.slice(5).trimStart()));
          if (collected.length) setLines((old) => old.concat(collected));
          // 流正常结束（后端超时/断开）：3s 后自动重订，最多 5 次
          if (!ctrl.signal.aborted && attempt < RETRY_LIMIT) {
            setConnected(false);
            retryTimer = setTimeout(() => { if (!ctrl.signal.aborted) void run(attempt + 1); }, RETRY_DELAY_MS);
          } else {
            finish(null);
          }
        } else {
          // 一次性：{pods:[...], logs:{pod:text}}
          const data = (await res.json()) as { pods?: unknown; logs?: Record<string, string> };
          const logs = data.logs ?? {};
          const blocks = Object.entries(logs).map(([pn, text]) =>
            '=== ' + pn + ' ===\n' + String(text ?? '').replace(/\n$/, ''));
          setLines(blocks);
          finish(null);
        }
      } catch (e) {
        if (ctrl.signal.aborted) return;
        if (follow && attempt < RETRY_LIMIT) {
          finish(null);
          retryTimer = setTimeout(() => void run(attempt + 1), RETRY_DELAY_MS);
        } else {
          finish(e instanceof Error ? e.message : String(e));
        }
      }
    };

    void run(0);

    return () => {
      ctrl.abort();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [ns, name, pod, follow]);

  return { lines, connected, error };
}

/**
 * Subscribe to an SSE watch stream for a kind and patch the TanStack Query
 * caches on ADDED / MODIFIED / DELETED events. Cleans up on unmount.
 */
export function useWatch(kind: ResourceKind): boolean {
  const qc = useQueryClient();
  const [live, setLive] = useState(false);

  useEffect(() => {
    const es = new EventSource(sseUrl('/watch/' + kind));
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    // 后端 watch.py 推送精简包络 {type,kind,ns,name,resourceVersion}（无 object），
    // 收到事件后以失效查询代替缓存补丁；命名事件为 upstream-error（避免与原生
    // onerror 重连语义冲突，见 watch.py）。
    const fail = (): void => setLive(false);
    es.addEventListener('upstream-error', fail as EventListener);
    es.onmessage = (ev: MessageEvent<string>) => {
      let event: { type?: string; kind?: string; ns?: string; name?: string; resourceVersion?: string };
      try {
        event = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (!event.name) return;
      // useList 的 query key 形态为 ['list', kind, ns]，detail 为 ['resource', kind, ns, name]
      void qc.invalidateQueries({
        predicate: (q) => q.queryKey[0] === 'list' && q.queryKey[1] === kind,
      });
      const detailKey = queryKeys.resource(kind, event.ns ?? '', event.name);
      if (event.type === 'DELETED') qc.removeQueries({ queryKey: detailKey });
      else void qc.invalidateQueries({ queryKey: detailKey });
    };
    return () => {
      es.close();
      setLive(false);
    };
  }, [kind, qc]);

  return live;
}

export { normalizeMetrics };
