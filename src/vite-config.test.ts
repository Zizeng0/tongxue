// @vitest-environment node
import { afterEach, expect, test, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

test('can point the development API proxy at a separate backend port', async () => {
  vi.stubEnv('TONGXUE_API_TARGET', 'http://127.0.0.1:8001')
  vi.resetModules()
  const { default: config } = await import('../vite.config')
  expect(config).toMatchObject({ server: { proxy: { '/api': 'http://127.0.0.1:8001', '/mcp': 'http://127.0.0.1:8001' } } })
})
