import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/selfcheck/**/*.test.ts', 'src/journey/**/*.journey.test.tsx'],
    // 旅程测试需要真实 DOM；既有纯函数契约测试保留 node 环境即可
    environment: 'jsdom',
    environmentMatchGlobs: [['src/selfcheck/**', 'node']],
    setupFiles: ['./src/test/setup.ts'],
    // globals 开启后 @testing-library/react 才能注册自动 cleanup（vitest 无全局 afterEach 时 DOM 会在用例间残留）
    globals: true,
  },
});
