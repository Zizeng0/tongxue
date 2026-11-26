import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Root from '../Root'
import { getModelSettings, getOutputSettings, previewModelConnection, updateModelSettings, updateOutputSettings } from '../api'
import SettingsPage from './SettingsPage'
import '../styles.css'

vi.mock('../api', () => ({
  readToken: () => 'session-token',
  getCurrentUser: async () => ({ id: 1, user_name: 'alice', identity: 0, status: 0 }),
  getSoftware: async () => ({ items: [], selected_id: 'hfss' }),
  getTasks: async () => ({ items: [] }),
  getModelSettings: vi.fn(),
  previewModelConnection: vi.fn(),
  updateModelSettings: vi.fn(),
  getOutputSettings: vi.fn(),
  updateOutputSettings: vi.fn(),
}))

beforeEach(() => {
  window.location.hash = '#/'
  localStorage.clear()
  vi.resetAllMocks()
  vi.mocked(getModelSettings).mockImplementation(async (role) => ({
    revision: null,
    current: `${role}-active`,
    options: [`${role}-active`, `${role}-next`],
    error: null,
  }))
  vi.mocked(updateModelSettings).mockResolvedValue({ revision: 'saved' })
})
afterEach(cleanup)

test('从设置列表进入模型配置，显示当前模型并提交变更', async () => {
  const user = userEvent.setup()
  render(<Root />)
  await user.click(await screen.findByRole('link', { name: '设置' }))
  expect(getModelSettings).not.toHaveBeenCalled()
  await user.click(await screen.findByRole('link', { name: '模型配置' }))
  const text = within(screen.getByRole('form', { name: '文本模型' }))
  const vision = within(screen.getByRole('form', { name: '视觉模型' }))
  expect(await text.findByRole('combobox')).toHaveValue('text-active')
  expect(await vision.findByRole('combobox')).toHaveValue('vision-active')
  expect(text.getByRole('button', { name: '提交' })).toBeDisabled()
  expect(vision.getByRole('button', { name: '提交' })).toBeDisabled()
  await user.selectOptions(text.getByRole('combobox'), 'text-next')
  await user.selectOptions(vision.getByRole('combobox'), 'vision-next')
  await user.click(text.getByRole('button', { name: '提交' }))
  expect(await text.findByText('模型配置已提交。')).toBeInTheDocument()
  expect(vision.queryByText('模型配置已提交。')).not.toBeInTheDocument()
  expect(vision.getByRole('combobox')).toHaveValue('vision-next')
  expect(updateModelSettings).toHaveBeenCalledExactlyOnceWith('text', null, 'text-next', undefined)
  await user.click(vision.getByRole('button', { name: '提交' }))
  expect(await vision.findByText('模型配置已提交。')).toBeInTheDocument()
  expect(updateModelSettings).toHaveBeenLastCalledWith('vision', null, 'vision-next', undefined)
  await user.click(screen.getByRole('link', { name: '返回设置列表' }))
  expect(await screen.findByRole('link', { name: '模型配置' })).toBeInTheDocument()
})

test('视觉密钥弹窗验证失败后可重试，只为视觉模型应用新连接', async () => {
  const user = userEvent.setup()
  const connection = { base_url: 'https://models.example/v1', api_key: 'example-key' }
  vi.mocked(previewModelConnection).mockRejectedValueOnce(new Error('模型服务拒绝认证')).mockResolvedValue(['new-vision'])
  render(<SettingsPage section="models" />)
  const text = within(screen.getByRole('form', { name: '文本模型' }))
  const vision = within(screen.getByRole('form', { name: '视觉模型' }))
  const edit = await vision.findByRole('button', { name: '更改api-key' })
  await user.click(edit)
  let dialog = within(screen.getByRole('dialog', { name: '视觉模型 · 更改api-key' }))
  await user.type(dialog.getByLabelText('api-key'), 'discarded')
  await user.click(dialog.getByRole('button', { name: '取消' }))
  expect(previewModelConnection).not.toHaveBeenCalled()
  await user.click(edit)
  dialog = within(screen.getByRole('dialog'))
  expect(dialog.getByLabelText('api-key')).toHaveValue('')
  expect(dialog.getByLabelText('api-key')).toHaveAttribute('type', 'password')
  await user.type(dialog.getByLabelText('base_url'), connection.base_url)
  await user.type(dialog.getByLabelText('api-key'), connection.api_key)
  await user.click(dialog.getByRole('button', { name: '保存' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('模型服务拒绝认证')
  await user.click(dialog.getByRole('button', { name: '保存' }))
  expect(await vision.findByText('已验证新连接。请选择模型并提交。')).toBeInTheDocument()
  expect(previewModelConnection).toHaveBeenLastCalledWith('vision', connection)
  expect(updateModelSettings).not.toHaveBeenCalled()
  expect(vision.getByRole('button', { name: '提交' })).toBeDisabled()
  expect(text.getByRole('combobox')).toHaveValue('text-active')
  expect(text.queryByRole('option', { name: 'new-vision' })).not.toBeInTheDocument()
  await user.selectOptions(vision.getByRole('combobox'), 'new-vision')
  await user.click(vision.getByRole('button', { name: '提交' }))
  expect(await vision.findByText('模型配置已提交。')).toBeInTheDocument()
  expect(updateModelSettings).toHaveBeenCalledExactlyOnceWith('vision', null, 'new-vision', connection)
  await user.click(text.getByRole('button', { name: '更改api-key' }))
  expect(within(screen.getByRole('dialog', { name: '文本模型 · 更改api-key' })).getByLabelText('api-key')).toHaveValue('')
})

test('列表加载失败时显示当前模型，可以刷新或更换连接', async () => {
  const user = userEvent.setup()
  vi.mocked(getModelSettings).mockResolvedValueOnce({
    revision: null,
    current: 'text-active', options: [], error: '模型服务拒绝认证',
  })
  render(<SettingsPage section="models" />)
  const text = within(screen.getByRole('form', { name: '文本模型' }))
  expect(await text.findByRole('combobox')).toHaveValue('text-active')
  expect(text.getByRole('status')).toHaveTextContent('模型服务拒绝认证')
  expect(text.getByRole('button', { name: '更改api-key' })).toBeEnabled()
  await user.click(text.getByRole('button', { name: '刷新列表' }))
  expect(await text.findByRole('option', { name: 'text-next' })).toBeInTheDocument()
  expect(vi.mocked(getModelSettings).mock.calls.filter(([role]) => role === 'vision')).toHaveLength(1)
})

test('外观设置支持字号预设和自定义值，并在重新打开时恢复', async () => {
  const user = userEvent.setup()
  const { unmount } = render(<Root />)
  await user.click(await screen.findByRole('link', { name: '设置' }))
  await user.click(await screen.findByRole('link', { name: '外观设置' }))
  const setting = screen.getByRole('region', { name: '字体大小' })
  const input = within(setting).getByRole('textbox', { name: '全局字体大小' })
  expect(input).toHaveValue('14.3')
  const toggle = within(setting).getByRole('button', { name: '字号预设' })
  expect(screen.queryByRole('group', { name: '可选字号' })).toBeNull()
  await user.click(toggle)
  const options = within(screen.getByRole('group', { name: '可选字号' }))
  expect(options.getByRole('button', { name: '14.3' })).toHaveAttribute('aria-pressed', 'true')
  await user.click(options.getByRole('button', { name: '20' }))
  expect(input).toHaveValue('20')
  expect(document.documentElement.style.getPropertyValue('--app-font-size')).toBe('20px')
  await user.clear(input)
  await user.type(input, '17.5{Enter}')
  expect(document.documentElement.style.getPropertyValue('--app-font-size')).toBe('17.5px')
  await user.clear(input)
  await user.type(input, '99')
  await user.tab()
  expect(input).toHaveValue('17.5')
  unmount()
  render(<Root />)
  expect(await screen.findByRole('textbox', { name: '全局字体大小' })).toHaveValue('17.5')
  expect(document.documentElement.style.getPropertyValue('--app-font-size')).toBe('17.5px')
})

test('从输出设置选择详略并保存账号设置', async () => {
  const user = userEvent.setup()
  vi.mocked(getOutputSettings).mockResolvedValue({ detail: 'normal' })
  vi.mocked(updateOutputSettings).mockResolvedValue({ detail: 'brief' })
  render(<Root />)
  await user.click(await screen.findByRole('link', { name: '设置' }))
  await user.click(await screen.findByRole('link', { name: '输出设置' }))
  const select = screen.getByRole('combobox', { name: '输出内容详略设置' })
  await waitFor(() => expect(select).toHaveValue('normal'))
  expect(within(select).getByRole('option', { name: '详细、丰富' })).toBeInTheDocument()
  expect(select.parentElement).toContainElement(screen.getByRole('button', { name: '保存' }))
  await user.selectOptions(select, 'detailed')
  const detailed = within(screen.getByLabelText('详细档的输出内容'))
  for (const name of ['PPT', '论文', '实验报告', '解题过程']) {
    expect(detailed.getByText(name)).toBeInTheDocument()
  }
  expect(detailed.getByText('详细档通常会增加 Token 消耗。')).toBeInTheDocument()
  await user.selectOptions(select, 'brief')
  expect(screen.queryByLabelText('详细档的输出内容')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '保存' }))
  expect(await screen.findByText('输出设置已保存。')).toBeInTheDocument()
  expect(updateOutputSettings).toHaveBeenCalledExactlyOnceWith('brief')
})
