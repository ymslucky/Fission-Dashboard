/**
 * K8s CRD shapes for fission.io/v1 resources.
 * Only commonly used metadata/spec fields are modelled; apiVersion/kind/metadata
 * follow the standard Kubernetes object envelope.
 */

export type ResourceKind =
  | 'functions'
  | 'packages'
  | 'environments'
  | 'httptriggers'
  | 'timetriggers';

export interface ObjectMeta {
  name: string;
  namespace: string;
  uid?: string;
  resourceVersion?: string;
  generation?: number;
  creationTimestamp?: string;
  labels?: Record<string, string>;
  annotations?: Record<string, string>;
}

export interface KubernetesObject {
  apiVersion: 'fission.io/v1';
  kind: string;
  metadata: ObjectMeta;
}

export interface FunctionSpec {
  environment: {
    name: string;
    namespace?: string;
  };
  package: {
    packageref: {
      name: string;
      namespace?: string;
      resourceversion?: string;
    };
    functionType?: string;
  };
  secrets?: { name: string; namespace?: string }[];
  configs?: { name: string; namespace?: string }[];
  resources?: {
    limits?: Record<string, string>;
    requests?: Record<string, string>;
  };
  concurrency?: number;
  requestsPerPod?: number;
  functionTimeout?: number;
  invocationCount?: number;
  dnsName?: string;
}

export interface FunctionStatus {
  ready?: boolean;
  replicas?: number;
  desiredReplicas?: number;
  invocationCount?: number;
  [k: string]: unknown;
}

export interface Function extends KubernetesObject {
  kind: 'Function';
  spec: FunctionSpec;
  status?: FunctionStatus;
}

export interface PackageSpec {
  environment: {
    name: string;
    namespace?: string;
  };
  status?: 'pending' | 'build' | 'none' | 'failed' | string;
  buildstatus?: string;
  source?: {
    checksum?: Record<string, string>;
    literal?: Record<string, string>;
    type?: string;
  };
  deployment?: {
    checksum?: Record<string, string>;
    literal?: Record<string, string>;
    type?: string;
  };
  builder?: {
    command?: string;
    container?: string;
  };
}

export interface Package extends KubernetesObject {
  kind: 'Package';
  spec: PackageSpec;
}

export interface EnvironmentSpec {
  version: number;
  runtime?: {
    image?: string;
    container?: string;
    podspec?: unknown;
  };
  builder?: {
    image?: string;
    command?: string;
    container?: string;
  };
  imagepullsecret?: string;
  allowAccessToExternalNetwork?: boolean;
  poolsize?: number;
  terminationGracePeriod?: number;
  keeparchive?: boolean;
  image?: string;
}

export interface Environment extends KubernetesObject {
  kind: 'Environment';
  spec: EnvironmentSpec;
}

export interface HTTPTriggerSpec {
  host?: string;
  relativeurl: string;
  createingress?: boolean;
  functionref: {
    name: string;
    namespace?: string;
  };
  method?: string;
  methods?: string[];
}

export interface HTTPTrigger extends KubernetesObject {
  kind: 'HTTPTrigger';
  spec: HTTPTriggerSpec;
}

export interface TimerTriggerSpec {
  cron: string;
  functionref: {
    name: string;
    namespace?: string;
  };
  stopTime?: string;
  startTime?: string;
}

export interface TimerTrigger extends KubernetesObject {
  kind: 'TimerTrigger';
  spec: TimerTriggerSpec;
}

export type FissionResource =
  | Function
  | Package
  | Environment
  | HTTPTrigger
  | TimerTrigger;

/** Aggregate data returned by /api/overview */
export interface Overview {
  counts?: {
    functions?: number;
    packages?: number;
    environments?: number;
    httpTriggers?: number;
    timeTriggers?: number;
    [key: string]: number | undefined;
  };
  version?: string;
  server?: string;
  [key: string]: unknown;
}

/** Metrics sample point returned by /api/metrics */
export interface MetricPoint {
  name: string;
  namespace?: string;
  timestamp?: string;
  values?: Record<string, number>;
}

export interface LogEntry {
  timestamp?: string;
  pod?: string;
  message: string;
}

/** Watch/SSE event envelope */
export interface WatchEvent<T = FissionResource> {
  type: 'ADDED' | 'MODIFIED' | 'DELETED' | 'SYNC';
  object: T;
}

/* ---- P0 additions: describe / test / pods / events ---- */

/** GET /api/functions/{ns}/{name}/describe (backend in progress; 501-fallback) */
export interface DescribeInfo {
  buildStatus?: string;
  warmPods?: number;
  invocable?: string;
  mcpExposed?: boolean | string;
  triggers?: Array<{ type?: string; name?: string; method?: string; host?: string; url?: string; cron?: string; [k: string]: unknown }>;
  pods?: PodInfo[];
  [k: string]: unknown;
}

export interface PodInfo {
  name: string;
  status?: string;
  ready?: boolean | string;
  restarts?: number;
  age?: string;
  ip?: string;
  [k: string]: unknown;
}

export interface FunctionEvent {
  timestamp?: string;
  type?: string;
  reason?: string;
  object?: string;
  message: string;
}

/** POST /api/functions/{ns}/{name}/test */
export interface TestRequest {
  method: string;
  path: string;
  headers?: Record<string, string>;
  body?: string;
}

export interface TestResult {
  status?: number;
  latencyMs?: number;
  requestId?: string;
  component?: string;
  reason?: string;
  body?: string;
  error?: boolean;
  [k: string]: unknown;
}

/** Runtime status of a function, synthesised from describe/pods data. */
export type FnStatus = 'ready' | 'error' | 'pending';
