import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppRoutes } from '../routes';
import { server } from '../test/msw';
import { http, HttpResponse } from 'msw';

/** 测试专用 QueryClient：不重试，避免网络层噪声掩盖用户旅程。 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

export function renderApp(initialEntries: string[]): void {
  render(
    <QueryClientProvider client={makeQueryClient()}>
      <AppRoutes initialEntries={initialEntries} />
    </QueryClientProvider>,
  );
}

/** Given 基线：从 /overview 出发，真实点击侧栏“函数”，再点列表行 quote 进入详情。 */
export async function givenOnQuoteDetail(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  renderApp(['/overview']);
  await user.click(await screen.findByRole('link', { name: /函数/ }));
  // 等列表页真实挂载（总览页 svg 点阵里也有 “quote” 字样，须先确认已离开总览）
  await screen.findByText('函数观测表');
  // 列表行渲染出 quote（fn-name 单元格），点击整行触发真实路由跳转
  await user.click(await screen.findByText('quote'));
  // 详情 Inspector 出现（describe 卡片标题）
  await screen.findByText('DESCRIBE · 构建与运行时');
}

/** J2 变体：用 server.use 覆盖 test 端点。 */
export function mockTestEndpoint(body: Record<string, unknown>): void {
  server.use(http.post('*/api/functions/fission/quote/test', () => HttpResponse.json(body)));
}