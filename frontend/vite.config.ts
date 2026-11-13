import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';
import { loadEnv } from 'vite';

export default defineConfig(({ command, mode }) => {
  const enabled =
    command === 'serve' &&
    mode === 'development' &&
    loadEnv(mode, process.cwd(), 'VITE_ENABLE_RESEARCH_API').VITE_ENABLE_RESEARCH_API === 'true';
  const researchPath =
    /^\/api\/v1\/research\/(summary|candidates(?:\/[1-9]\d*)?|snapshots|automation-status)$/;
  const overviewEnabled =
    command === 'serve' &&
    mode === 'development' &&
    loadEnv(mode, process.cwd(), 'VITE_ENABLE_OVERVIEW_API').VITE_ENABLE_OVERVIEW_API === 'true';
  const overviewPath = /^\/api\/v1\/overview\/(summary|positions|performance|recommendations)$/;
  return {
    plugins: [
      react(),
      tailwindcss(),
      {
        name: 'local-research-read-boundary',
        configureServer(server) {
          server.middlewares.use((request, response, next) => {
            if (request.url?.startsWith('/api/')) {
              const path = new URL(request.url, 'http://127.0.0.1').pathname;
              if (
                request.method !== 'GET' ||
                !(
                  (enabled && researchPath.test(path)) ||
                  (overviewEnabled && overviewPath.test(path))
                )
              ) {
                response.statusCode = 403;
                response.end('Read-only local API boundary');
                return;
              }
            }
            next();
          });
        },
      },
    ],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: {
        ...(enabled
          ? {
              '/api/v1/research/': {
                target: 'http://127.0.0.1:8080',
                followRedirects: false,
                proxyTimeout: 5000,
              },
            }
          : {}),
        ...(overviewEnabled
          ? {
              '/api/v1/overview/': {
                target: 'http://127.0.0.1:8080',
                followRedirects: false,
                proxyTimeout: 5000,
              },
            }
          : {}),
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      restoreMocks: true,
    },
  };
});
