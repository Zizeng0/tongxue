import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import Root from '../Root'
import { clearToken, saveToken } from '../api'

const project = { id: 'project-1', title: '传感器实验', created_at: '2026-11-25', software_id: 'hfss' }
const file = { name: '实验材料.pdf', path: 'messages/turn-1/other-1.pdf', size: 100 }

function startSession(identity: number) {
  saveToken('existing-login')
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/mcp/' && JSON.parse(String(options?.body)).params.name === 'preview_pdf_page') {
      return Response.json({ result: { content: [{ type: 'image', mimeType: 'image/jpeg', data: 'dGVzdA==' }] } })
    }
    const responses: Record<string, unknown> = {
      '/api/users/me': { id: 1, user_name: '测试账号', identity, status: 0, image: '' },
      '/api/software': { items: [], selected_id: null },
      '/api/tasks': { items: [] },
      '/api/projects': { items: [project] },
      '/api/projects/project-1': { ...project, guide: null, messages: [{ inputs: [file], outputs: [] }] },
    }
    return Response.json(responses[url])
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  cleanup()
  clearToken()
  window.location.hash = ''
  vi.unstubAllGlobals()
})

test('opens administrator MCP and renders only the active five PDF pages using the login JWT', async () => {
  const user = userEvent.setup()
  const fetchMock = startSession(1)
  window.location.hash = '#/'
  render(<Root />)

  const navigation = await screen.findByRole('navigation', { name: '主导航' })
  const link = within(navigation).getByRole('link', { name: 'MCP 服务' })
  await user.click(link)
  await screen.findByRole('heading', { name: 'MCP 服务', level: 1 })
  expect(link).toHaveAttribute('aria-current', 'page')

  fetchMock.mockResolvedValueOnce(Response.json({ result: { structuredContent: { papers: [{
    title: 'Pressure Sensors', doi: '10.1234/demo', url: 'https://doi.org/10.1234/demo', year: 2026,
    authors: 'Example Author', journal: 'Measurement', abstract: 'Study of pressure sensors.',
  }] } } }))
  await user.type(screen.getByRole('textbox', { name: '检索关键词' }), 'pressure sensors')
  await user.click(screen.getByRole('button', { name: '检索 Crossref' }))
  expect(await screen.findByRole('link', { name: 'Pressure Sensors' })).toHaveAttribute('href', 'https://doi.org/10.1234/demo')

  await user.selectOptions(screen.getByRole('combobox', { name: '项目' }), project.id)
  await screen.findByRole('option', { name: file.name })
  expect(screen.getByRole('combobox', { name: 'PDF 文件' })).toHaveValue(file.path)
  const end = screen.getByRole('spinbutton', { name: '结束页' })
  await user.clear(end)
  await user.type(end, '7')
  const pages = Array.from({ length: 7 }, (_, index) => ({
    page: index + 1, source: index === 1 ? 'no_text_layer' : 'native', text: index === 1 ? '' : `Content ${index + 1}`,
  }))
  fetchMock.mockResolvedValueOnce(Response.json({ result: { structuredContent: { pages, truncated: false } } }))
  await user.click(screen.getByRole('button', { name: '读取页码' }))
  expect(await screen.findByRole('img', { name: 'PDF 第 5 页预览' })).toHaveAttribute('src', 'data:image/jpeg;base64,dGVzdA==')
  expect(screen.getByText('本页没有可读取的文字层。')).toBeInTheDocument()
  expect(screen.queryByText('Content 6')).not.toBeInTheDocument()
  expect(screen.getAllByRole('img', { name: /^PDF 第/ })).toHaveLength(5)
  expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
  expect(fetchMock.mock.calls.filter(([url]) => url === '/mcp/')).toHaveLength(7)

  await user.click(screen.getByRole('button', { name: '下一页' }))
  expect(await screen.findByRole('img', { name: 'PDF 第 7 页预览' })).toBeInTheDocument()
  expect(screen.getAllByRole('img', { name: /^PDF 第/ })).toHaveLength(2)
  expect(screen.queryByText('Content 1')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  const calls = fetchMock.mock.calls.filter(([url]) => url === '/mcp/')
  expect(calls).toHaveLength(9)
  const requests = calls.map(([, options]) => {
    const headers = new Headers(options?.headers)
    expect(headers.get('Authorization')).toBe('Bearer existing-login')
    expect(headers.get('Accept')).toBe('application/json, text/event-stream')
    return JSON.parse(String(options?.body))
  })
  expect(requests.slice(0, 2)).toMatchObject([
    { jsonrpc: '2.0', method: 'tools/call', params: { name: 'search_papers', arguments: { query: 'pressure sensors' } } },
    { jsonrpc: '2.0', method: 'tools/call', params: { name: 'read_pdf_pages',
      arguments: { conversation_id: project.id, file_id: file.path, start_page: 1, end_page: 7 } } },
  ])
  expect(requests.slice(2).map(({ params }) => params)).toEqual(pages.map(({ page }) => ({
    name: 'preview_pdf_page', arguments: { conversation_id: project.id, file_id: file.path, page_number: page },
  })))
  await user.click(screen.getByRole('button', { name: '上一页' }))
  expect(await screen.findByRole('img', { name: 'PDF 第 1 页预览' })).toBeInTheDocument()
  expect(screen.queryByRole('img', { name: 'PDF 第 7 页预览' })).not.toBeInTheDocument()
})

test('a regular account cannot open the MCP page from its hash route', async () => {
  const fetchMock = startSession(0)
  window.location.hash = '#/mcp'
  render(<Root />)
  const navigation = await screen.findByRole('navigation', { name: '主导航' })
  expect(within(navigation).queryByRole('link', { name: 'MCP 服务' })).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'MCP 服务' })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([url]) => url === '/api/projects' || url === '/mcp/')).toBe(false)
})

test('runs an artifact tool from the authenticated MCP page and shows its file', async () => {
  const user = userEvent.setup()
  const fetchMock = startSession(1)
  window.location.hash = '#/mcp'
  render(<Root />)

  const tools = within(await screen.findByRole('region', { name: '生成与绘图工具' }))
  const run = tools.getByRole('button', { name: '运行工具' })
  const args = tools.getByRole('textbox', { name: '参数（JSON）' })
  await user.clear(args)
  await user.type(args, 'null')
  await user.click(run)
  expect(tools.getByRole('alert')).toHaveTextContent('请输入有效的 JSON 对象')
  expect(fetchMock.mock.calls.some(([url]) => url === '/mcp/')).toBe(false)

  const choice = tools.getByRole('combobox', { name: '工具' })
  await user.selectOptions(choice, 'render_thesis')
  expect(JSON.parse((args as HTMLTextAreaElement).value).manuscript.title).toBe('校园雨水回收研究')
  const mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  fetchMock.mockResolvedValueOnce(Response.json({ result: { content: [
    { type: 'resource', resource: { mimeType, blob: 'dGVzdA==' } },
  ] } }))
  await user.click(run)
  const download = await tools.findByRole('link', { name: '下载 manuscript.docx' })
  expect(download).toHaveAttribute('download', 'manuscript.docx')
  expect(download).toHaveAttribute('href', `data:${mimeType};base64,dGVzdA==`)

  await user.selectOptions(choice, 'plot_discrete_sequence')
  fetchMock.mockResolvedValueOnce(Response.json({ result: { content: [
    { type: 'image', data: 'dGVzdA==', mimeType: 'image/png' },
  ] } }))
  await user.click(run)
  expect(await tools.findByRole('img', { name: '离散序列图' })).toHaveAttribute('src', 'data:image/png;base64,dGVzdA==')
  const calls = fetchMock.mock.calls.filter(([url]) => url === '/mcp/')
  expect(calls).toHaveLength(2)
  for (const [, options] of calls) {
    expect(new Headers(options?.headers).get('Authorization')).toBe('Bearer existing-login')
  }
  expect(JSON.parse(String(calls[1][1]?.body)).params).toEqual({
    name: 'plot_discrete_sequence', arguments: { samples: [0, 1, 2, 1, 0], start_index: -2, title: '离散序列' },
  })
})

test('submits a confirmed experiment and follows its status', async () => {
  const user = userEvent.setup()
  const fetchMock = startSession(1)
  window.location.hash = '#/mcp'
  const originalFetch = fetchMock.getMockImplementation()
  fetchMock.mockImplementation(async (url, options) => {
    if (url === '/api/projects') return Response.json({ items: [{ ...project, kind: 'task' }] })
    if (url === '/mcp/') {
      const { name, arguments: args } = JSON.parse(String(options?.body)).params
      const state = { conversation_id: args.conversation_id, request_id: args.request_id }
      return Response.json({ result: { structuredContent: name === 'submit_experiment_task'
        ? { ...state, status: 'queued' } : { ...state, status: 'answered', reply: '实验流程完成' } } })
    }
    return originalFetch!(url, options)
  })
  render(<Root />)
  const tools = within(await screen.findByRole('region', { name: '提交实验任务' }))
  await tools.findByRole('option', { name: '传感器实验 · hfss' })
  await user.selectOptions(tools.getByRole('combobox', { name: '实验项目' }), project.id)
  await user.type(tools.getByRole('textbox', { name: '实验要求' }), '核对实验参数')
  const submit = tools.getByRole('button', { name: '提交任务' })
  expect(submit).toBeDisabled()
  await user.click(tools.getByRole('checkbox', { name: '确认在本机后台执行此实验工作流' }))
  await user.click(submit)
  expect(await tools.findByRole('heading', { name: '排队中' })).toBeInTheDocument()
  expect(await tools.findByRole('heading', { name: '工作流已完成' }, { timeout: 4000 })).toBeInTheDocument()
  expect(tools.getByRole('link', { name: '查看实验项目' })).toHaveAttribute('href', '#/projects/project-1')
  const calls = fetchMock.mock.calls.filter(([url]) => url === '/mcp/').map(([, options]) => {
    expect(new Headers(options?.headers).get('Authorization')).toBe('Bearer existing-login')
    return JSON.parse(String(options?.body)).params
  })
  const requestId = calls[0].arguments.request_id
  expect(calls).toEqual([
    { name: 'submit_experiment_task', arguments: { conversation_id: project.id, software_id: 'hfss',
      requirements: '核对实验参数', deliverables: ['report'], request_id: requestId, confirm_execution: true } },
    { name: 'get_experiment_task', arguments: { conversation_id: project.id, request_id: requestId } },
  ])
})
