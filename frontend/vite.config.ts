import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';
import { loadEnv, type Connect, type Plugin, type ProxyOptions } from 'vite';
import { resolveDashboardApis } from './dashboard-api-options.ts';

const readPaths = {
  research:
    /^\/api\/v1\/research\/(summary|candidates(?:\/[1-9]\d*)?|snapshots|automation-status)$/,
  overview: /^\/api\/v1\/overview\/(summary|positions|performance|recommendations)$/,
  analysis: /^\/api\/v1\/ai-trade-analysis\/recommendations(?:\/[1-9]\d{0,18}(?:\/candidates)?)?$/,
};

function dashboardApiBoundary(apis: ReturnType<typeof resolveDashboardApis>): Plugin {
  const enabledReads = (Object.keys(readPaths) as (keyof typeof readPaths)[])
    .filter((api) => apis[api])
    .map((api) => readPaths[api]);
  const boundary: Connect.NextHandleFunction = (request, response, next) => {
    const path = request.url?.split('?', 1)[0] ?? '';
    if (path !== '/api' && !path.startsWith('/api/')) return next();

    response.setHeader('Cache-Control', 'no-store');
    const allowedRead = request.method === 'GET' && enabledReads.some((read) => read.test(path));
    const allowedAuth =
      enabledReads.length > 0 &&
      ((request.method === 'GET' && /^\/api\/v1\/auth\/(csrf|me)$/.test(path)) ||
        (request.method === 'POST' && /^\/api\/v1\/auth\/(login|logout)$/.test(path)));
    if (allowedRead || allowedAuth) return next();
    response.statusCode = 403;
    response.end('Read-only dashboard API boundary');
  };
  return {
    name: 'dashboard-read-boundary',
    configureServer(server) {
      server.middlewares.use(boundary);
    },
    configurePreviewServer(server) {
      server.middlewares.use(boundary);
    },
  };
}

function dashboardProxy(port: number): Record<string, ProxyOptions> {
  return {
    '/api/': {
      target: 'http://127.0.0.1:' + port,
      followRedirects: false,
      proxyTimeout: 5000,
      configure(proxy) {
        proxy.on('proxyRes', (response) => {
          response.headers['cache-control'] = 'no-store';
        });
      },
    },
  };
}

export default defineConfig(({ command, mode, isPreview }) => {
  const localPreview = isPreview === true;
  const localDev = command === 'serve' && !localPreview && mode === 'development';
  const env = localDev ? loadEnv(mode, process.cwd(), 'VITE_ENABLE_') : {};
  const apis = resolveDashboardApis(localPreview, localDev, {
    research: env.VITE_ENABLE_RESEARCH_API,
    overview: env.VITE_ENABLE_OVERVIEW_API,
    analysis: env.VITE_ENABLE_AI_ANALYSIS_API,
  });
  const previewBackendPort = Number(process.env.DASHBOARD_PREVIEW_BACKEND_PORT ?? 8080);
  if (
    localPreview &&
    (!Number.isInteger(previewBackendPort) || previewBackendPort < 1 || previewBackendPort > 65535)
  ) {
    throw new Error('DASHBOARD_PREVIEW_BACKEND_PORT must be an integer between 1 and 65535');
  }

  return {
    plugins: [react(), tailwindcss(), dashboardApiBoundary(apis)],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy:
        localDev && (apis.research || apis.overview || apis.analysis) ? dashboardProxy(8080) : {},
    },
    preview: { proxy: localPreview ? dashboardProxy(previewBackendPort) : {} },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      restoreMocks: true,
    },
  };
});
