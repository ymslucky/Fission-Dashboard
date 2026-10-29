import { asList } from './client';
/**
 * P1/P2 API surface: versions/rollback, canary, MCP, GitOps, mesh, audit,
 * whoami, prom. All non-CRD endpoints beyond the P0 core live here.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from './client';

const enc = encodeURIComponent;
const seg = (s: string): string => enc(s);

/* ---------------- whoami ---------------- */

export interface Whoami {
  user: string;
  groups?: string[];
  admin?: boolean;
}

export function useWhoami() {
  return useQuery<Whoami>({
    queryKey: ['whoami'],
    queryFn: () => api.get<Whoami>('/whoami'),
    staleTime: 60_000,
    retry: false,
  });
}

/* ---------------- versions & rollback ---------------- */

export interface VersionInfo {
  name: string;
  /** BE (versions.py) 返回 created；creationTimestamp 仅作旧响应兜底 */
  created?: string;
  creationTimestamp?: string;
  functionExists?: boolean;
}

export interface VersionsResponse {
  versions: VersionInfo[];
  aliases: { name: string; target: string }[];
}

export function useFunctionVersionList(ns: string, name: string) {
  return useQuery<VersionsResponse>({
    queryKey: ['versions2', ns, name],
    queryFn: () => api.get<VersionsResponse>('/functions/' + seg(ns) + '/' + seg(name) + '/versions'),
    enabled: Boolean(ns && name),
    retry: false,
  });
}

export interface RollbackResult {
  coldStartWarning?: string;
  [k: string]: unknown;
}

export function useRollback(ns: string, name: string) {
  const qc = useQueryClient();
  return useMutation<RollbackResult, Error, { alias: string; toVersion: string }>({
    mutationFn: (body) => api.post<RollbackResult>('/functions/' + seg(ns) + '/' + seg(name) + '/rollback', body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['versions2', ns, name] });
    },
  });
}

/* ---------------- canary ---------------- */

/** GET /canaryconfigs 返回 CRD 包络：metadata.{name,namespace} + spec.{七字段} */
export interface CanaryConfig {
  metadata?: { name?: string; namespace?: string; [k: string]: unknown };
  spec?: {
    newfunction?: string;
    oldfunction?: string;
    trigger?: string;
    weightincrement?: number;
    duration?: string;
    failurethreshold?: number;
    failureType?: 'failure' | 'timeout' | string;
    [k: string]: unknown;
  };
  [k: string]: unknown;
}

/** GET /canaryconfigs/{ns}/{name}/status（canary.py 实际 return 形态） */
export interface CanaryStatus {
  name?: string;
  ns?: string;
  /** 阶段字符串：status.status ?? status.stage ?? 'unknown'（BE 已归一） */
  stage?: string;
  status?: Record<string, unknown>;
  functionWeights?: Record<string, number> | null;
  failures?: number;
  weightincrement?: number;
  duration?: string;
  failurethreshold?: number;
  failureType?: string;
  [k: string]: unknown;
}

export function useCanaryConfigs() {
  return useQuery<CanaryConfig[]>({
    queryKey: ['canaryconfigs'],
    queryFn: async () => asList<CanaryConfig>(await api.get<unknown>('/canaryconfigs')),
    retry: false,
  });
}

export function useCanaryStatus(ns: string, name: string, enabled = true) {
  return useQuery<CanaryStatus>({
    queryKey: ['canarystatus', ns, name],
    queryFn: () => api.get<CanaryStatus>('/canaryconfigs/' + seg(ns) + '/' + seg(name) + '/status'),
    enabled: enabled && Boolean(ns && name),
    refetchInterval: 5_000,
    retry: false,
  });
}

export function useCanaryCreate() {
  const qc = useQueryClient();
  return useMutation<CanaryConfig, Error, { ns: string; body: { apiVersion?: string; kind?: string; metadata: { name: string; namespace: string }; spec: NonNullable<CanaryConfig['spec']> } }>({
    mutationFn: ({ body }) => api.post<CanaryConfig>('/canaryconfigs', body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['canaryconfigs'] });
    },
  });
}

export function useCanaryUpdate() {
  const qc = useQueryClient();
  return useMutation<CanaryConfig, Error, { ns: string; name: string; body: { name: string; namespace: string; spec: NonNullable<CanaryConfig['spec']> } }>({
    mutationFn: ({ ns, name, body }) => api.put<CanaryConfig>('/canaryconfigs/' + seg(ns) + '/' + seg(name), body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['canaryconfigs'] });
    },
  });
}

export function useCanaryDelete() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, { ns: string; name: string }>({
    mutationFn: ({ ns, name }) => api.delete('/canaryconfigs/' + seg(ns) + '/' + seg(name)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['canaryconfigs'] });
    },
  });
}

/* ---------------- httptrigger weights ---------------- */

export function useSetTriggerWeights(ns: string, name: string) {
  const qc = useQueryClient();
  return useMutation<unknown, Error, { weights: Record<string, number> }>({
    mutationFn: (body) => api.put('/httptriggers/' + seg(ns) + '/' + seg(name) + '/weights', body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['canarystatus'] });
    },
  });
}

/* ---------------- MCP ---------------- */

export interface McpConfig {
  enabled: boolean;
  message?: string;
}

/** /mcp/tools 元素：BE (mcp.py) 返回 ns/name/toolDescription/inputSchema（无 enabled，
 * 列表本身即暴露标记，故 listed 即 exposed） */
export interface McpTool {
  ns?: string;
  name?: string;
  toolDescription?: string;
  inputSchema?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface McpToken {
  name?: string;
  allowedNamespaces?: string[];
  /** Unix 秒（mcptoken.list_tokens 返回 claim.exp） */
  expiresAt?: number;
  issuedAt?: number;
  expired?: boolean;
  [k: string]: unknown;
}

export function useMcpConfig() {
  return useQuery<McpConfig>({
    queryKey: ['mcpconfig'],
    queryFn: () => api.get<McpConfig>('/mcp/config'),
    retry: false,
  });
}

export function useMcpTools(enabled: boolean) {
  return useQuery<McpTool[]>({
    queryKey: ['mcptools'],
    queryFn: async () => asList<McpTool>(await api.get<unknown>('/mcp/tools'), 'tools'),
    enabled,
    retry: false,
  });
}

export function useSetFunctionMcp() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, { ns: string; name: string; body: { enabled: boolean; toolDescription?: string; inputSchema?: Record<string, unknown> } }>({
    mutationFn: ({ ns, name, body }) =>
      api.put('/functions/' + seg(ns) + '/' + seg(name) + '/mcp', body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['mcptools'] });
    },
  });
}

export function useMcpTokens(enabled: boolean) {
  return useQuery<McpToken[]>({
    queryKey: ['mcptokens'],
    queryFn: async () => asList<McpToken>(await api.get<unknown>('/mcp/tokens'), 'tokens'),
    enabled,
    retry: false,
  });
}

export function useMcpTokenCreate() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, { name: string; allowedNamespaces: string[]; ttlDays: number }>({
    mutationFn: (body) => api.post('/mcp/tokens', body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['mcptokens'] });
    },
  });
}

export function useMcpTokenRevoke() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, { name: string }>({
    mutationFn: ({ name }) => api.delete('/mcp/tokens/' + seg(name)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['mcptokens'] });
    },
  });
}

/* ---------------- GitOps ---------------- */

export type GitopsSyncState = 'synced' | 'repo-only' | 'repo-changed' | 'cluster-changed' | 'conflict' | string;

/** tree.resources 元素（gitops.py gitops_tree） */
export interface GitopsResourceRow {
  path?: string;
  kind?: string;
  ns?: string;
  name?: string;
  state?: GitopsSyncState;
  [k: string]: unknown;
}

/** tree.files 元素：仅文件浏览用 */
export interface GitopsFileNode {
  path?: string;
  size?: number;
  [k: string]: unknown;
}

export interface GitopsTree {
  repo?: string;
  branch?: string;
  files?: GitopsFileNode[];
  resources?: GitopsResourceRow[];
}

/** plan 条目（gitops.py gitops_plan_v2）：path/kind/ns/name/key（conflict 另有 hint） */
export interface GitopsPlanItem {
  path?: string;
  kind?: string;
  ns?: string;
  name?: string;
  key?: string;
  hint?: string;
  [k: string]: unknown;
}

export interface GitopsPlan {
  repo?: string;
  branch?: string;
  files?: number;
  create?: GitopsPlanItem[];
  update?: GitopsPlanItem[];
  delete?: GitopsPlanItem[];
  conflict?: GitopsPlanItem[];
  unchanged?: GitopsPlanItem[];
  [k: string]: unknown;
}

/** status：BE 返回计数 map {create,update,delete,conflict,unchanged} */
export interface GitopsStatusCounts {
  create?: number;
  update?: number;
  delete?: number;
  conflict?: number;
  unchanged?: number;
  [k: string]: unknown;
}

export function useGitopsStatus(src: string) {
  return useQuery<GitopsStatusCounts>({
    queryKey: ['gitopsstatus', src],
    queryFn: () => api.get<GitopsStatusCounts>('/gitops/' + seg(src) + '/status'),
    enabled: Boolean(src),
    retry: false,
  });
}

export function useGitopsTree(src: string) {
  return useQuery<GitopsTree>({
    queryKey: ['gitopstree', src],
    queryFn: () => api.get<GitopsTree>('/gitops/' + seg(src) + '/tree'),
    enabled: Boolean(src),
    retry: false,
  });
}

/** file：BE 返回 {path, content} */
export function useGitopsFile(src: string, path: string | null) {
  return useQuery<{ path?: string; content?: string }>({
    queryKey: ['gitopsfile', src, path],
    queryFn: () => api.get<{ path?: string; content?: string }>('/gitops/' + seg(src) + '/file?path=' + enc(path ?? '')),
    enabled: Boolean(src && path),
    retry: false,
  });
}

export function useGitopsPlan(src: string) {
  return useQuery<GitopsPlan>({
    queryKey: ['gitopsplan', src],
    queryFn: () => api.get<GitopsPlan>('/gitops/' + seg(src) + '/plan'),
    enabled: Boolean(src),
    retry: false,
  });
}

/** apply 请求体（misc.py ApplyPayload）：deletes=要删除的 key 列表，conflicts={key:'repo'|'cluster'} */
export function useGitopsApply(src: string) {
  return useMutation<unknown, Error, { deletes: string[]; conflicts: Record<string, 'repo' | 'cluster'> }>({
    mutationFn: (body) => api.post('/gitops/' + seg(src) + '/apply', body),
  });
}

export interface GitopsExportResult {
  prUrl?: string;
  pr?: string;
  message?: string;
  [k: string]: unknown;
}

export function useGitopsExport(src: string) {
  return useMutation<GitopsExportResult, Error, { mode: string; resources: string[] }>({
    mutationFn: (body) => api.post<GitopsExportResult>('/gitops/' + seg(src) + '/export', body),
  });
}

/* ---------------- mesh ---------------- */

export interface MeshProbe {
  istio: boolean | null;
  linkerd: boolean | null;
}

export function useMeshProbe() {
  return useQuery<MeshProbe>({
    queryKey: ['meshprobe'],
    queryFn: () => api.get<MeshProbe>('/mesh/probe'),
    retry: false,
  });
}

export interface PromResult {
  result?: Array<{ value?: [number, string]; values?: [number, string][]; metric?: Record<string, string> }>;
  [k: string]: unknown;
}

export function useProm(query: string | null) {
  return useQuery<PromResult>({
    queryKey: ['prom', query],
    queryFn: () => api.get<PromResult>('/prom/' + enc(query ?? '')),
    enabled: Boolean(query),
    retry: false,
  });
}

/** Extract a scalar number from a Prometheus instant-vector response. */
export function promScalar(res: PromResult | undefined): number | null {
  const v = res?.result?.[0]?.value?.[1];
  if (v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** 501=未配置 PROMETHEUS_URL，404=端点缺失，400=白名单外，502=prometheus 不可达 */
export function isPromUnavailable(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 501 || err.status === 404 || err.status === 400 || err.status === 502);
}

/** 区分格内短因：白名单外 / 未配置 / 不可达 */
export function promErrorReason(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status === 400) return '白名单外';
  if (err.status === 502) return 'prometheus 不可达';
  if (err.status === 501 || err.status === 404) return 'PROMETHEUS 未配置';
  return null;
}

/* ---------------- audit ---------------- */

export interface AuditEntry {
  ts?: string;
  timestamp?: string;
  user?: string;
  action?: string;
  kind?: string;
  ns?: string;
  name?: string;
  resource?: string;
  summary?: string;
  [k: string]: unknown;
}

export function useAudit(limit: number) {
  return useQuery<AuditEntry[]>({
    queryKey: ['audit', limit],
    queryFn: async () => asList<AuditEntry>(await api.get<unknown>('/audit?limit=' + limit)),
    retry: false,
  });
}