import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

/* ---- 基线 fixtures（真实 CRD 字段，v1.27.0） ---- */

export const quoteFunction = {
  apiVersion: 'fission.io/v1',
  kind: 'Function',
  metadata: { name: 'quote', namespace: 'fission', uid: 'uid-quote-1', resourceVersion: '42' },
  spec: {
    environment: { name: 'python' },
    InvokeStrategy: { ExecutionStrategy: { ExecutorType: 'poolmgr' } },
    package: { packageref: { name: 'quote-pkg' } },
    resources: { limits: { cpu: '500m' } },
  },
};

export const quoteHttpTriggerWeighted = {
  apiVersion: 'fission.io/v1',
  kind: 'HTTPTrigger',
  metadata: { name: 'quote-http', namespace: 'fission', uid: 'uid-trig-1' },
  spec: {
    functionref: { type: 'function-weights', name: '', functionweights: { quote: 80, 'quote-v2': 20 } },
    relativeurl: '/quote',
    methods: ['GET'],
  },
};

export const quoteDescribe = {
  function: quoteFunction,
  package: { name: 'quote-pkg', buildStatus: 'succeeded', buildlog: '', object: null },
  pods: { warm: 2, ready: 2 },
  executorType: 'poolmgr',
  invocable: true,
  invocableNote: 'Yes (cold start on first call)',
  triggers: [{ kind: 'httptriggers', ns: 'fission', name: 'quote-http', url: '/quote' }],
  canary: [],
  mcpToolExposed: { enabled: false, toolDescription: '', inputSchema: {} },
};

export const testOk = {
  status: 200,
  latencyMs: 59,
  requestId: 'rid-1',
  component: 'function',
  reason: 'ok',
  body: '{"service":"quote API"}',
  headers: { 'x-fission-request-id': 'rid-1' },
};

export const test404 = {
  status: 404,
  latencyMs: 8,
  requestId: '',
  component: 'router',
  reason: 'function or route not found',
  body: '',
  headers: {},
};

export const testTimeout = {
  status: null,
  latencyMs: 60000,
  requestId: '',
  component: 'router',
  reason: 'fission function timeout (60s，Fission 默认 60s)',
  body: '',
  headers: {},
};

const environment = {
  apiVersion: 'fission.io/v1',
  kind: 'Environment',
  metadata: { name: 'python', namespace: 'fission', uid: 'uid-env-1' },
  spec: { version: 2, runtime: { image: 'fission/python-env' } },
};

const quotePackage = {
  apiVersion: 'fission.io/v1',
  kind: 'Package',
  metadata: { name: 'quote-pkg', namespace: 'fission', uid: 'uid-pkg-1' },
  spec: { environment: { name: 'python' } },
  status: { buildstatus: { status: 'succeeded' } },
};

/** 基线 handlers：所有路径与 BFF 同源 /api/... 一致。 */
export const handlers = [
  http.get('*/api/resources/functions', () => HttpResponse.json({ items: [quoteFunction] })),
  http.get('*/api/resources/httptriggers', () => HttpResponse.json({ items: [quoteHttpTriggerWeighted] })),
  http.get('*/api/resources/environments', () => HttpResponse.json({ items: [environment] })),
  http.get('*/api/resources/packages', () => HttpResponse.json({ items: [quotePackage] })),
  http.get('*/api/resources/timetriggers', () => HttpResponse.json({ items: [] })),
  http.get('*/api/functions/fission/quote/describe', () => HttpResponse.json(quoteDescribe)),
  http.post('*/api/functions/fission/quote/test', () => HttpResponse.json(testOk)),
  http.get('*/api/functions/fission/quote/pods', () => HttpResponse.json({ items: [] })),
  http.get('*/api/functions/fission/quote/events', () => HttpResponse.json({ items: [] })),
  http.get('*/api/functions/fission/quote/logs', () => new HttpResponse(null, { status: 404 })),
  http.get('*/api/overview', () => HttpResponse.json({
    counts: { functions: 1, packages: 1, environments: 1, httptriggers: 1, timetriggers: 0 },
    core: [{ app: 'fission', pods: 3, ready: 3 }],
  })),
  http.get('*/api/whoami', () => HttpResponse.json({ user: 'tester', groups: ['fission-admins'], admin: true })),
  http.get('*/api/metrics', () => HttpResponse.json({
    functions: { quote: { ns: 'fission', pods: 2, ready: 2, cpu_m: 55, mem_mi: 130 } },
    ts: 0,
  })),
  http.get('*/api/watch/:kind', () => new HttpResponse(null, { status: 404 })),
];
/** MSW node server：在 setup.ts 中统一启停，测试内用 server.use 覆盖。 */
export const server = setupServer(...handlers);
