import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from 'vitest'

test('the directly opened index uses committed classic-script assets', () => {
  const root = resolve(process.cwd())
  const html = readFileSync(resolve(root, 'index.html'), 'utf8')

  expect(html).toContain('href="./dist/standalone.css"')
  expect(html).toContain('src="./dist/standalone.js"')
  expect(html).not.toContain('type="module"')
  expect(existsSync(resolve(root, 'dist/standalone.css'))).toBe(true)
  expect(existsSync(resolve(root, 'dist/standalone.js'))).toBe(true)
})
