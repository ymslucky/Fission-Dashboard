import { lazy, Suspense, useMemo, useState, type ReactElement } from 'react';
import { createBrowserRouter, createMemoryRouter, Link, NavLink, Outlet, RouterProvider, useLocation, useNavigate } from 'react-router-dom';
import { useOverview, useList, useWatch } from './api/hooks';
import { useWhoami } from './api/extend';
import { CommandPalette, ToastProvider } from './components';
import type { Command } from './components';

const Overview = lazy(() => import('./pages/Overview'));
const Functions = lazy(() => import('./pages/Functions'));
const Packages = lazy(() => import('./pages/Packages'));
const Environments = lazy(() => import('./pages/Environments'));
const HTTPTriggers = lazy(() => import('./pages/HTTPTriggers'));
const TimerTriggers = lazy(() => import('./pages/TimerTriggers'));
const Tester = lazy(() => import('./pages/Tester'));
const Versions = lazy(() => import('./pages/Versions'));
const Canary = lazy(() => import('./pages/Canary'));
const Mcp = lazy(() => import('./pages/Mcp'));
const GitOps = lazy(() => import('./pages/GitOps'));
const Mesh = lazy(() => import('./pages/Mesh'));
const Audit = lazy(() => import('./pages/Audit'));

const NAV: { path: string; zh: string; en: string }[] = [
  { path: '/overview', zh: '总览', en: 'OVERVIEW' },
  { path: '/functions', zh: '函数', en: 'FUNCTIONS' },
  { path: '/packages', zh: '包', en: 'PACKAGES' },
  { path: '/environments', zh: '环境', en: 'ENVIRONMENTS' },
  { path: '/httptriggers', zh: 'HTTP 触发器', en: 'HTTP TRIGGERS' },
  { path: '/timetriggers', zh: '定时触发器', en: 'TIME TRIGGERS' },
  { path: '/canary', zh: '金丝雀', en: 'CANARY' },
  { path: '/versions', zh: '版本', en: 'VERSIONS' },
  { path: '/gitops', zh: 'GitOps', en: 'GITOPS' },
  { path: '/mcp', zh: 'MCP', en: 'MCP' },
  { path: '/mesh', zh: '网格', en: 'MESH' },
  { path: '/audit', zh: '审计', en: 'AUDIT' },
];

function WhoamiChip(): ReactElement | null {
  const who = useWhoami();
  if (who.isLoading || who.isError || !who.data?.user) return null;
  return (
    <span className="chip" title={who.data.admin ? '管理员' : (who.data.groups ?? []).join(', ') || undefined}>
      <span className="kind-dot" style={{ background: who.data.admin ? 'var(--kind-canary)' : '#4a8fa3' }} />
      {who.data.user}
      {who.data.admin ? ' · 管理员' : ''}
    </span>
  );
}

function StatusChips(): ReactElement {
  const overview = useOverview();
  const watchLive = useWatch('functions');
  const counts = overview.data?.counts;

  return (
    <div className="chips">
      <span className="chip">
        <span className="kind-dot" style={{ background: overview.isError ? 'var(--alarm)' : '#4a8fa3' }} />
        {overview.isError ? 'fission API 不可达' : 'fission API 正常'}
      </span>
      <span className="chip">
        <span className="kind-dot" style={{ background: watchLive ? '#4a8fa3' : 'var(--gray-teal)' }} />
        {watchLive ? 'watch 实时' : 'watch 轮询 15s'}
      </span>
      {counts ? <span className="chip">fn {counts.functions ?? '—'} · pkg {counts.packages ?? '—'}</span> : null}
      <WhoamiChip />
    </div>
  );
}

function Shell(): ReactElement {
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const functions = useList('functions');

  const commands = useMemo<Command[]>(() => {
    const jumps: Command[] = NAV.map((n) => ({
      id: 'go:' + n.path,
      label: '跳转 · ' + n.zh,
      hint: n.en,
      run: () => navigate(n.path),
    }));
    const fns: Command[] = (functions.data ?? []).map((f) => ({
      id: 'fn:' + f.metadata.namespace + '/' + f.metadata.name,
      label: '函数 · ' + f.metadata.name,
      hint: f.metadata.namespace,
      run: () => navigate('/functions/' + encodeURIComponent(f.metadata.namespace) + '/' + encodeURIComponent(f.metadata.name)),
    }));
    return [...fns, ...jumps];
  }, [functions.data, navigate]);

  return (
    <div className="app">
      <header className="topbar">
        <button type="button" className="menu-btn" onClick={() => setNavOpen((o) => !o)} aria-label="导航">☰</button>
        <div className="brand">FISSION CONSOLE<small>观测站</small></div>
        <StatusChips />
      </header>
      <div className="shell">
        <nav className={'sidenav' + (navOpen ? ' open' : '')}>
          {NAV.map((n) => (
            <NavLink
              key={n.path}
              to={n.path}
              className={location.pathname.startsWith(n.path) ? 'on' : undefined}
              onClick={() => setNavOpen(false)}
            >
              <span>{n.zh}</span>
              <span className="en">{n.en}</span>
            </NavLink>
          ))}
          <div style={{ padding: '10px 18px' }}>
            <hr className="hairline" />
            <button type="button" className="btn sm" style={{ marginTop: 8 }} onClick={() => navigate('/tester')}>
              调用测试台
            </button>
          </div>
        </nav>
        <Suspense fallback={<div className="page page-pad muted">加载中…</div>}>
          <Outlet />
        </Suspense>
      </div>
      <CommandPalette commands={commands} />
    </div>
  );
}

function NotFound(): ReactElement {
  return (
    <main className="page page-pad">
      <div className="empty">
        <p>该观测记录不存在。</p>
        <p><Link to="/overview">返回总览</Link></p>
      </div>
    </main>
  );
}

const routeTable = [
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Overview /> },
      { path: 'overview', element: <Overview /> },
      { path: 'functions', element: <Functions /> },
      { path: 'functions/:ns/:name', element: <Functions /> },
      { path: 'packages', element: <Packages /> },
      { path: 'environments', element: <Environments /> },
      { path: 'httptriggers', element: <HTTPTriggers /> },
      { path: 'timetriggers', element: <TimerTriggers /> },
      { path: 'tester', element: <Tester /> },
      { path: 'canary', element: <Canary /> },
      { path: 'versions', element: <Versions /> },
      { path: 'gitops', element: <GitOps /> },
      { path: 'mcp', element: <Mcp /> },
      { path: 'mesh', element: <Mesh /> },
      { path: 'audit', element: <Audit /> },
      { path: '*', element: <NotFound /> },
    ],
  },
];

export const router = createBrowserRouter(routeTable);

/**
 * 应用路由树。生产入口不传参（BrowserRouter）；旅程测试传 initialEntries，
 * 用 MemoryRouter 形态从指定 URL 起步，壳层（顶条/导航/命令面板）保持真实组件树。
 */
export function AppRoutes(options?: { initialEntries?: string[] }): ReactElement {
  const router = options?.initialEntries
    ? createMemoryRouter(routeTable, { initialEntries: options.initialEntries })
    : createBrowserRouter(routeTable);
  return (
    <ToastProvider>
      <RouterProvider router={router} />
    </ToastProvider>
  );
}