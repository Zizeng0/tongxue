import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App, { formatElapsed } from './App'
import { createTask, deleteTask, downloadReport, getRoommateChats, getSoftware, getSolutions, getTaskProgress, getTasks, selectSoftware, updateMe, uploadAvatar } from './api'
import './styles.css'

vi.mock('./api', () => ({ assetUrl: (path: string) => path, getSoftware: vi.fn(), getSolutions: vi.fn(), getRoommateChats: vi.fn(), getTaskProgress: vi.fn(), getTasks: vi.fn(), selectSoftware: vi.fn(), createTask: vi.fn(), deleteTask: vi.fn(), downloadReport: vi.fn(), updateMe: vi.fn(), uploadAvatar: vi.fn() }))

const getSoftwareMock = vi.mocked(getSoftware)
const getTasksMock = vi.mocked(getTasks)
const getSolutionsMock = vi.mocked(getSolutions)
const getTaskProgressMock = vi.mocked(getTaskProgress)
const selectSoftwareMock = vi.mocked(selectSoftware)
const createTaskMock = vi.mocked(createTask)
const deleteTaskMock = vi.mocked(deleteTask)
const downloadReportMock = vi.mocked(downloadReport)
const updateMeMock = vi.mocked(updateMe)
const uploadAvatarMock = vi.mocked(uploadAvatar)
const software = [
  { id: 'modelsim', name: 'Modelsim SE-64 10.1c' },
  { id: 'quartus', name: 'Quartus II 13.1 (64-bit)' },
  { id: 'packet-tracer', name: 'Cisco Packet Tracer' },
  { id: 'hfss', name: 'Ansys HFSS' },
  { id: 'pycharm', name: 'PyCharm' },
]

afterEach(cleanup)
beforeEach(() => {
  vi.resetAllMocks()
  getSoftwareMock.mockResolvedValue({ items: software, selected_id: 'hfss' })
  getTasksMock.mockResolvedValue({ items: [] })
  getSolutionsMock.mockResolvedValue({ items: [] })
  vi.mocked(getRoommateChats).mockResolvedValue({ items: [] })
  getTaskProgressMock.mockResolvedValue({ stage: '模型正在分析任务', done: false })
  selectSoftwareMock.mockImplementation(async (softwareId) => ({ selected_id: softwareId }))
  deleteTaskMock.mockResolvedValue(undefined)
  createTaskMock.mockResolvedValue({ id: 'test-task', title: '分析参数变化', created_at: '2026-11-09T00:00:00Z', messages: [{
    id: 'first-message', created_at: '2026-11-09T00:00:00Z', requirements: '分析参数变化',
    guide_name: 'guide.pdf', attachment_names: ['figure.png'],
  }] })
})

test('formats elapsed processing time without leading empty units', () => {
  expect(formatElapsed(8.9)).toBe('8秒')
  expect(formatElapsed(70)).toBe('1分10秒')
  expect(formatElapsed(3661)).toBe('1时1分1秒')
})

test('shows the requested card order without the previous workflow and promotional block', async () => {
  render(<App />)
  await screen.findByRole('button', { name: '实验软件' })

  const cards = Array.from(document.querySelectorAll('.workspace-grid > section')).map((element) => element.querySelector('h2')?.textContent)
  expect(cards).toEqual(['任务图片', '用户输入'])
  expect(document.querySelector('.topbar__guide')).toHaveTextContent('指导文件')
  expect(document.querySelector('.topbar__guide')).toHaveTextContent('最大 120 MB')
  expect(document.querySelector('.topbar__guide')!.compareDocumentPosition(document.querySelector('.topbar__software')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(document.querySelector('.topbar__software .software-picker')).toBeInTheDocument()
  expect(document.querySelector('.topbar__location .service-pill')).toBeInTheDocument()
  expect(screen.queryByText('解析与规划')).not.toBeInTheDocument()
  expect(screen.queryByText('从输入到交付')).not.toBeInTheDocument()
  expect(screen.queryByText('保存任务草稿')).not.toBeInTheDocument()
  expect(document.querySelector('.requirements-panel .composer button[type="submit"]')).toBe(screen.getByRole('button', { name: '发送' }))
  expect(screen.getByRole('heading', { name: '历史对话信息' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '新增会话' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: '对话记录' })).toBeEmptyDOMElement()
  const page = document.querySelector<HTMLElement>('.page-wrap')!
  const conversation = screen.getByRole('region', { name: '对话记录' })
  expect(page.firstElementChild).toBe(conversation)
  expect(window.getComputedStyle(page).paddingBottom).toBe('5vh')
  expect(window.getComputedStyle(conversation).flexGrow).toBe('1')
  expect(window.getComputedStyle(document.querySelector('.workspace-grid')!).alignItems).toBe('stretch')
  expect(screen.queryByRole('heading', { name: /新建实验任务/ })).not.toBeInTheDocument()
  expect(document.querySelector('.requirements-heading .deliverables')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '源文件' })).toHaveAttribute('aria-pressed', 'false')
  expect(window.getComputedStyle(document.querySelector('.deliverable--report')!).width).toBe('100px')
  expect(document.querySelector('.submit-bar')).not.toBeInTheDocument()
})

test('offers a separate solve page in the main navigation', () => {
  render(<App view="solve" />)
  expect(screen.getByRole('link', { name: '实验工作台' })).toHaveAttribute('href', '#/')
  expect(screen.getByRole('link', { name: '解题' })).toHaveAttribute('href', '#/solve')
  expect(screen.getByRole('link', { name: '解题' })).toHaveClass('nav-item--active')
  expect(screen.getByRole('region', { name: '解题对话' })).toBeInTheDocument()
  expect(screen.queryByText('实验软件')).not.toBeInTheDocument()
})

test('opens the roommate module alongside the original solve page', async () => {
  render(<App view="roommate" />)
  const navigation = screen.getByRole('navigation', { name: '主导航' })
  expect(screen.getByRole('link', { name: '室友' })).toHaveAttribute('aria-current', 'page')
  expect(navigation.querySelector('[href="#/solve"]')).toHaveTextContent('解题')
  expect(await screen.findByText('暂无历史对话')).toBeInTheDocument()
  expect(screen.getByRole('region', { name: '室友对话' })).toBeInTheDocument()
})

test('source file deliverable is selected only after a click', async () => {
  const user = userEvent.setup()
  render(<App />)
  const sourceFiles = screen.getByRole('button', { name: '源文件' })
  expect(sourceFiles).toHaveAttribute('aria-pressed', 'false')
  await user.click(sourceFiles)
  expect(sourceFiles).toHaveAttribute('aria-pressed', 'true')
})

test('profile editor uploads the chosen avatar and saves user details', async () => {
  const currentUser = { id: 1, user_name: 'alice', identity: 0, status: 0, image: '' }
  const changed = vi.fn()
  uploadAvatarMock.mockResolvedValue({ ...currentUser, image: '/api/users/avatars/saved.png' })
  updateMeMock.mockResolvedValue({ ...currentUser, image: '/api/users/avatars/saved.png' })
  render(<App user={currentUser} onUserChange={changed} />)
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '用户头像' }))
  await user.click(screen.getByRole('button', { name: '个人信息' }))
  const avatar = new File(['image'], 'avatar.png', { type: 'image/png' })
  await user.upload(screen.getByLabelText('上传头像'), avatar)
  expect(window.getComputedStyle(screen.getByLabelText('上传头像')).paddingTop).toBe('4px')
  await user.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(uploadAvatarMock).toHaveBeenCalledWith(avatar))
  expect(updateMeMock).toHaveBeenCalledWith({ user_name: 'alice' })
  expect(changed).toHaveBeenCalledWith(expect.objectContaining({ image: '/api/users/avatars/saved.png' }))
})

test('avatar menu closes when clicking elsewhere', async () => {
  const user = userEvent.setup()
  render(<App user={{ id: 1, user_name: 'alice', identity: 0, status: 0, image: '' }} />)
  const avatar = screen.getByRole('button', { name: '用户头像' })
  await user.click(avatar)
  expect(avatar).toHaveAttribute('aria-expanded', 'true')
  await user.click(screen.getByRole('heading', { name: '历史对话信息' }))
  expect(avatar).toHaveAttribute('aria-expanded', 'false')
})

test('Enter sends the request while Shift Enter keeps a newline', async () => {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('本地服务已连接')
  fireEvent.change(document.querySelector('.topbar__guide input[type="file"]')!, {
    target: { files: [new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })] },
  })
  const composer = screen.getByRole('textbox', { name: '实验要求' })
  await user.type(composer, '第一行{Shift>}{Enter}{/Shift}第二行')
  expect(composer).toHaveValue('第一行\n第二行')
  expect(createTaskMock).not.toHaveBeenCalled()
  await user.type(composer, '{Enter}')
  await waitFor(() => expect(createTaskMock).toHaveBeenCalledOnce())
  expect(createTaskMock.mock.calls[0][0].requirements).toBe('第一行\n第二行')
})

test('opens the software menu and sends selections to the API', async () => {
  const user = userEvent.setup()
  render(<App />)
  const picker = await screen.findByRole('button', { name: '实验软件' })
  expect(picker).toHaveTextContent('Ansys HFSS')
  await user.click(picker)
  expect(picker).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(software.map((item) => item.name))

  await user.click(screen.getByRole('option', { name: 'Quartus II 13.1 (64-bit)' }))
  await waitFor(() => expect(selectSoftwareMock).toHaveBeenCalledWith('quartus'))
  expect(picker).toHaveTextContent('Quartus II 13.1 (64-bit)')
  expect(picker).toHaveAttribute('aria-expanded', 'false')
})

test('restores the software used by a historical experiment', async () => {
  const user = userEvent.setup()
  getTasksMock.mockResolvedValue({ items: [{ id: 'old', title: '仿真实验', created_at: '2026-11-18T00:00:00Z',
    messages: [{ id: 'm1', created_at: '2026-11-18T00:00:00Z', requirements: '仿真实验',
      guide_name: 'guide.pdf', attachment_names: [], software_id: 'modelsim' }] }] })
  render(<App />)
  await screen.findByRole('button', { name: '仿真实验' })
  await user.click(screen.getByRole('button', { name: '仿真实验' }))
  expect(screen.getByRole('button', { name: '实验软件' })).toHaveTextContent('Modelsim SE-64 10.1c')
})

test('selects HFSS and submits its software ID', async () => {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('本地服务已连接')

  await user.click(screen.getByRole('button', { name: '实验软件' }))
  await user.click(screen.getByRole('option', { name: 'Ansys HFSS' }))
  await waitFor(() => expect(selectSoftwareMock).toHaveBeenCalledWith('hfss'))

  const guideInput = document.querySelector<HTMLInputElement>('.topbar__guide input[type="file"]')!
  fireEvent.change(guideInput, { target: { files: [new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })] } })
  await user.type(screen.getByRole('textbox', { name: '实验要求' }), '规划天线仿真')
  await user.click(screen.getByRole('button', { name: '发送' }))

  await waitFor(() => expect(createTaskMock).toHaveBeenCalledOnce())
  expect(createTaskMock.mock.calls[0][0].software_id).toBe('hfss')
})

test('software menu stays usable when the local service is offline', async () => {
  getSoftwareMock.mockRejectedValueOnce(new Error('offline'))
  const user = userEvent.setup()
  render(<App />)
  const picker = screen.getByRole('button', { name: '实验软件' })
  await screen.findByText('本地服务未连接')

  await user.click(picker)
  await user.click(screen.getByRole('option', { name: 'Cisco Packet Tracer' }))
  expect(picker).toHaveTextContent('Cisco Packet Tracer')
  expect(selectSoftwareMock).not.toHaveBeenCalled()
})

test('submits guide, task attachments, requirements and software as a draft', async () => {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('本地服务已连接')
  const taskInput = document.querySelector<HTMLInputElement>('.task-panel input[type="file"]')!
  const guideInput = document.querySelector<HTMLInputElement>('.topbar__guide input[type="file"]')!
  const guide = new File(['%PDF-1.7\nexample'], 'guide.pdf', { type: 'application/pdf' })
  const image = new File(['image'], 'figure.png', { type: 'image/png' })
  const resetTaskInput = vi.spyOn(taskInput, 'value', 'set')
  fireEvent.change(taskInput, { target: { files: [image] } })
  expect(resetTaskInput).toHaveBeenCalledWith('')
  fireEvent.change(guideInput, { target: { files: [guide] } })
  expect(document.querySelector('.topbar__guide .dropzone__filename')).toHaveTextContent('guide.pdf')
  expect(document.querySelector('.task-panel .dropzone__filename')).toHaveTextContent('figure.png')
  expect(document.querySelectorAll('.dropzone__add')).toHaveLength(2)
  const openGuidePicker = vi.spyOn(guideInput, 'click').mockImplementation(() => {})
  await user.click(document.querySelector('.topbar__guide .dropzone__add')!)
  expect(openGuidePicker).toHaveBeenCalledOnce()
  await user.type(screen.getByRole('textbox', { name: '实验要求' }), '分析参数变化')
  await user.click(screen.getByRole('button', { name: '发送' }))

  await waitFor(() => expect(createTaskMock).toHaveBeenCalledOnce())
  const request = createTaskMock.mock.calls[0][0]
  expect(request).toEqual({
    guide, report_template: null, attachments: [image], other_files: [], requirements: '分析参数变化', conversation_id: null,
    software_id: 'hfss', deliverables: ['report'], request_id: expect.any(String),
  })
  const conversation = screen.getByRole('region', { name: '对话记录' })
  expect(conversation).toHaveTextContent('分析参数变化')
  expect(conversation.querySelector('.conversation__file[title="guide.pdf"]')).toBeInTheDocument()
  expect(conversation.querySelector('.conversation__file[title="figure.png"]')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: /新建实验任务/ })).not.toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '历史任务' })).toHaveTextContent('分析参数变化')
  expect(document.querySelector('.breadcrumb strong')).toHaveTextContent('分析参数变化')
  expect(screen.getByRole('textbox', { name: '实验要求' })).toHaveValue('')
  expect(document.querySelector('.topbar__guide-name')).toHaveTextContent('guide.pdf')
  expect(document.querySelector('.topbar__guide input[type="file"]')).not.toBeInTheDocument()
})

test('report template picker appears beside report and uploads with the guide', async () => {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('本地服务已连接')
  const reportButton = screen.getByRole('button', { name: '实验报告' })
  const templatePicker = screen.getByLabelText('上传实验报告模板')
  expect(templatePicker.closest('.deliverable')!.nextElementSibling).toBe(reportButton)

  const template = new File(['docx'], '学校模板.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
  fireEvent.change(templatePicker, { target: { files: [template] } })
  expect(templatePicker.closest('.deliverable')).toHaveTextContent('学校模板.docx')
  fireEvent.change(document.querySelector('.topbar__guide input[type="file"]')!, {
    target: { files: [new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })] },
  })
  await user.type(screen.getByRole('textbox', { name: '实验要求' }), '生成实验报告')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(createTaskMock).toHaveBeenCalledOnce())
  expect(createTaskMock.mock.calls[0][0].report_template).toBe(template)
})

test('accepts a DOC report template and other experimental image', async () => {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('本地服务已连接')
  const templatePicker = screen.getByLabelText('上传实验报告模板')
  expect(templatePicker).toHaveAttribute('accept', expect.stringContaining('.doc,'))
  const template = new File(['legacy'], '学校模板.doc', { type: 'application/msword' })
  const image = new File(['image'], 'sample.png', { type: 'image/png' })
  expect(screen.getByRole('button', { name: '其他文件 +' })).toBeInTheDocument()
  fireEvent.change(templatePicker, { target: { files: [template] } })
  fireEvent.change(document.querySelector('.task-panel__other input[type="file"]')!, { target: { files: [image] } })
  expect(document.querySelector('.task-panel__other .dropzone__filename')).toHaveTextContent('sample.png')
  fireEvent.change(document.querySelector('.topbar__guide input[type="file"]')!, {
    target: { files: [new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })] },
  })
  await user.type(screen.getByRole('textbox', { name: '实验要求' }), '处理图片')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(createTaskMock).toHaveBeenCalledOnce())
  expect(createTaskMock.mock.calls[0][0].report_template).toBe(template)
  expect(createTaskMock.mock.calls[0][0].other_files).toEqual([image])
})

test('generated report is downloadable from its conversation', async () => {
  getTasksMock.mockResolvedValueOnce({ items: [{
    id: 'report-task', title: '报告任务', created_at: '2026-11-09T00:00:00Z', messages: [{
      id: 'message', created_at: '2026-11-09T00:00:00Z', requirements: '生成报告', guide_name: 'guide.pdf',
      report_template_name: '学校模板.docx', report_files: ['experiment_report.docx'], attachment_names: [],
    }],
  }] })
  downloadReportMock.mockResolvedValue(undefined)
  const user = userEvent.setup()
  render(<App />)
  await user.click(await screen.findByRole('button', { name: '下载报告 · experiment_report.docx' }))
  expect(downloadReportMock).toHaveBeenCalledWith('report-task', 'message', 'experiment_report.docx')
})

test('shows a new conversation immediately while the server processes it', async () => {
  let finishRequest!: (conversation: Awaited<ReturnType<typeof createTask>>) => void
  createTaskMock.mockImplementationOnce(() => new Promise((resolve) => { finishRequest = resolve }))
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('本地服务已连接')
  const guide = new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })
  fireEvent.change(document.querySelector('.topbar__guide input[type="file"]')!, { target: { files: [guide] } })
  await user.type(screen.getByRole('textbox', { name: '实验要求' }), '立即显示实验')
  await user.click(screen.getByRole('button', { name: '发送' }))

  expect(createTaskMock).toHaveBeenCalledOnce()
  expect(screen.getByRole('textbox', { name: '实验要求' })).toHaveValue('')
  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('立即显示实验')
  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('正在处理')
  expect(screen.getByRole('article', { name: '助手回复' })).toHaveTextContent('模型正在分析任务')
  expect(screen.getByText('已处理 0秒')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('模型正在分析任务'))
  expect(getTaskProgressMock).toHaveBeenCalledWith(createTaskMock.mock.calls[0][0].request_id)
  expect(screen.getByRole('navigation', { name: '历史任务' })).toHaveTextContent('立即显示实验')
  expect(screen.getByRole('button', { name: '归档会话 立即显示实验' })).toBeDisabled()

  await act(async () => finishRequest({
    id: 'saved', title: '立即显示实验', created_at: '2026-11-15T10:39:00Z', messages: [{
      id: 'message', created_at: '2026-11-15T10:39:00Z', requirements: '立即显示实验',
      guide_name: 'guide.pdf', attachment_names: [], reply: '分析完成', status: 'answered', processing_seconds: 2.5,
    }],
  }))
  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('分析完成')
  expect(screen.getByText('已处理 2秒')).toBeInTheDocument()
  expect(window.getComputedStyle(screen.getByText('分析完成')).fontSize).toBe(window.getComputedStyle(screen.getByRole('textbox', { name: '实验要求' })).fontSize)
  expect(screen.getByRole('region', { name: '对话记录' })).not.toHaveTextContent('正在处理')
  expect(screen.getByRole('navigation', { name: '历史任务' }).querySelectorAll('.history-list__item')).toHaveLength(1)
})

test('keeps separate conversations visible and refreshes their replies while both run', async () => {
  const user = userEvent.setup()
  getTasksMock.mockResolvedValueOnce({ items: [] })
  createTaskMock.mockImplementation(async (request) => ({
    id: request.requirements, title: request.requirements, created_at: new Date().toISOString(),
    messages: [{ id: request.requirements, created_at: new Date().toISOString(), requirements: request.requirements,
      guide_name: 'guide.pdf', attachment_names: [], status: 'processing', request_id: request.request_id }],
  }))
  render(<App />)
  await screen.findByText('本地服务已连接')

  for (const name of ['第一项实验', '第二项实验']) {
    const guide = new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })
    fireEvent.change(document.querySelector('.topbar__guide input[type="file"]')!, { target: { files: [guide] } })
    await user.type(screen.getByRole('textbox', { name: '实验要求' }), name)
    await user.click(screen.getByRole('button', { name: '发送' }))
    await screen.findByRole('button', { name })
    expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('正在处理')
    if (name === '第一项实验') await user.click(screen.getByRole('button', { name: '新增会话' }))
  }

  expect(createTaskMock).toHaveBeenCalledTimes(2)
  expect(screen.getByRole('navigation', { name: '历史任务' }).querySelectorAll('.history-list__item')).toHaveLength(2)
  await user.click(screen.getByRole('button', { name: '第一项实验' }))
  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('第一项实验')
  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()

  getTasksMock.mockResolvedValue({ items: ['第一项实验', '第二项实验'].map((name) => ({
    id: name, title: name, created_at: new Date().toISOString(), messages: [{
      id: name, created_at: new Date().toISOString(), requirements: name, guide_name: 'guide.pdf',
      attachment_names: [], status: 'answered' as const, reply: `${name}完成`,
    }],
  })) })
  await waitFor(() => expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('第一项实验完成'), { timeout: 5000 })
  await user.click(screen.getByRole('button', { name: '第二项实验' }))
  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('第二项实验完成')
})

test('restores a running conversation after returning to the workspace', async () => {
  const running = { id: 'restored', title: '恢复实验', created_at: new Date().toISOString(), messages: [{
    id: 'message', created_at: new Date().toISOString(), requirements: '恢复实验', guide_name: 'guide.pdf',
    attachment_names: [], status: 'processing' as const, request_id: 'request-restored',
  }] }
  getTasksMock.mockResolvedValueOnce({ items: [running] })
  const { unmount } = render(<App />)
  expect(await screen.findByText('正在处理', { selector: '.conversation__status' })).toBeInTheDocument()
  unmount()
  getTasksMock.mockResolvedValueOnce({ items: [running] })
  render(<App />)
  expect(await screen.findByText('正在处理', { selector: '.conversation__status' })).toBeInTheDocument()
  expect(getTaskProgressMock).toHaveBeenCalledWith('request-restored')
  expect(screen.getByRole('navigation', { name: '历史任务' })).toHaveTextContent('恢复实验')
})

test('polls progress serially every three seconds while the local clock updates', async () => {
  let finishRequest!: (conversation: Awaited<ReturnType<typeof createTask>>) => void
  createTaskMock.mockImplementationOnce(() => new Promise((resolve) => { finishRequest = resolve }))
  const guide = new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })
  render(<App />)
  await screen.findByText('本地服务已连接')
  fireEvent.change(document.querySelector('.topbar__guide input[type="file"]')!, { target: { files: [guide] } })
  fireEvent.change(screen.getByRole('textbox', { name: '实验要求' }), { target: { value: '检查进度' } })
  vi.useFakeTimers()
  try {
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await act(async () => { await Promise.resolve() })
    expect(getTaskProgressMock).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(getTaskProgressMock).toHaveBeenCalledTimes(1)
    expect(screen.getByText('已处理 2秒')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(getTaskProgressMock).toHaveBeenCalledTimes(2)
    await act(async () => finishRequest({ id: 'saved', title: '检查进度', created_at: '2026-11-17T00:00:00Z', messages: [] }))
    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    expect(getTaskProgressMock).toHaveBeenCalledTimes(2)
  } finally {
    vi.useRealTimers()
  }
})

test('shows follow-up requests immediately without duplicating history', async () => {
  getTasksMock.mockResolvedValue({ items: [{
    id: 'existing', title: '电路仿真', created_at: '2026-11-08T00:00:00Z', messages: [{
      id: 'first', created_at: '2026-11-08T00:00:00Z', requirements: '先分析', guide_name: 'guide.pdf', attachment_names: [],
    }],
  }] })
  let finishRequest!: (conversation: Awaited<ReturnType<typeof createTask>>) => void
  createTaskMock.mockImplementationOnce(() => new Promise((resolve) => { finishRequest = resolve }))
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('先分析')
  await user.type(screen.getByRole('textbox', { name: '实验要求' }), '再检查')
  await user.click(screen.getByRole('button', { name: '发送' }))

  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('再检查')
  expect(screen.getByRole('textbox', { name: '实验要求' })).toHaveValue('')
  expect(screen.getByRole('navigation', { name: '历史任务' }).querySelectorAll('.history-list__item')).toHaveLength(1)

  await act(async () => finishRequest({ id: 'existing', title: '电路仿真', created_at: '2026-11-08T00:00:00Z', messages: [
    { id: 'first', created_at: '2026-11-08T00:00:00Z', requirements: '先分析', guide_name: 'guide.pdf', attachment_names: [] },
    { id: 'second', created_at: '2026-11-15T10:39:00Z', requirements: '再检查', guide_name: null, attachment_names: [], reply: '检查完成', status: 'answered' },
  ] }))
  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('检查完成')
  expect(screen.getAllByText('再检查', { selector: '.conversation__text' })).toHaveLength(1)
})

test('loads saved conversations into the main area and history sidebar', async () => {
  getTasksMock.mockResolvedValue({ items: [{
    id: 'older-task', title: '完成波形分析', created_at: '2026-11-08T00:00:00Z', messages: [{
      id: 'older-message', created_at: '2026-11-08T00:00:00Z', requirements: '完成波形分析',
      guide_name: 'very-long-guide-name.pdf', attachment_names: ['waveform-screenshot.png'],
    }],
  }] })
  render(<App />)

  expect(await screen.findByText('完成波形分析', { selector: '.conversation__text' })).toBeInTheDocument()
  expect(document.querySelector('.history-list__date')).toHaveTextContent(/创建于.*\d{2}:\d{2}/)
  expect(screen.queryByRole('heading', { name: /新建实验任务/ })).not.toBeInTheDocument()
  expect(document.querySelector('.conversation__file[title="very-long-guide-name.pdf"]')).toBeInTheDocument()
  expect(document.querySelector('.conversation__file[title="waveform-screenshot.png"]')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '完成波形分析' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('tooltip', { hidden: true })).toHaveTextContent(/2026年11月8日/)
  expect(document.querySelector('.breadcrumb strong')).toHaveTextContent('完成波形分析')
  expect(document.querySelector('.topbar__guide-name')).toHaveTextContent('very-long-guide-name.pdf')
  expect(document.querySelector('.topbar__guide input[type="file"]')).not.toBeInTheDocument()
})

test('switches conversations and starts a new one without losing history', async () => {
  const user = userEvent.setup()
  getTasksMock.mockResolvedValue({ items: [
    { id: 'first', title: '第一项实验', created_at: '2026-11-07T00:00:00Z', messages: [{ id: 'one', created_at: '2026-11-07T00:00:00Z', requirements: '第一项实验。分析结果', guide_name: 'one.pdf', attachment_names: [] }] },
    { id: 'second', title: '第二项实验', created_at: '2026-11-08T00:00:00Z', messages: [{ id: 'two', created_at: '2026-11-08T00:00:00Z', requirements: '第二项实验。验证参数', guide_name: 'two.pdf', attachment_names: [] }] },
  ] })
  render(<App />)
  expect(await screen.findByText('第二项实验。验证参数')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '第一项实验' }))
  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('第一项实验。分析结果')
  expect(document.querySelector('.topbar__guide-name')).toHaveTextContent('one.pdf')
  expect(screen.getByRole('region', { name: '对话记录' })).not.toHaveTextContent('第二项实验。验证参数')
  await user.click(screen.getByRole('button', { name: '新增会话' }))
  expect(screen.getByRole('region', { name: '对话记录' })).toBeEmptyDOMElement()
  expect(document.querySelector('.topbar__guide input[type="file"]')).toBeInTheDocument()
  expect(document.querySelector('.page-wrap')?.firstElementChild).toBe(screen.getByRole('region', { name: '对话记录' }))
  expect(document.querySelector('.breadcrumb strong')).toHaveTextContent('新建任务')
  expect(screen.getByRole('navigation', { name: '历史任务' }).querySelectorAll('.history-list__item')).toHaveLength(2)
})

test('keeps a new conversation selected when history finishes loading later', async () => {
  const user = userEvent.setup()
  let finishHistory!: (value: Awaited<ReturnType<typeof getTasks>>) => void
  getTasksMock.mockImplementationOnce(() => new Promise((resolve) => { finishHistory = resolve }))
  render(<App />)

  await user.click(screen.getByRole('button', { name: '新增会话' }))
  finishHistory({ items: [{
    id: 'older-task', title: '历史实验', created_at: '2026-11-08T00:00:00Z', messages: [{
      id: 'older-message', created_at: '2026-11-08T00:00:00Z', requirements: '历史实验', guide_name: 'guide.pdf', attachment_names: [],
    }],
  }] })

  expect(await screen.findByRole('button', { name: '历史实验' })).toBeInTheDocument()
  expect(document.querySelector('.breadcrumb strong')).toHaveTextContent('新建任务')
  expect(screen.getByRole('region', { name: '对话记录' })).toBeEmptyDOMElement()
})

test('sends a follow-up to the selected conversation without another guide', async () => {
  const user = userEvent.setup()
  getTasksMock.mockResolvedValue({ items: [{
    id: 'existing', title: '电路仿真', created_at: '2026-11-08T00:00:00Z', messages: [{
      id: 'first', created_at: '2026-11-08T00:00:00Z', requirements: '电路仿真。画图', guide_name: 'guide.pdf', attachment_names: [],
    }],
  }] })
  createTaskMock.mockResolvedValueOnce({ id: 'existing', title: '电路仿真', created_at: '2026-11-08T00:00:00Z', messages: [
    { id: 'first', created_at: '2026-11-08T00:00:00Z', requirements: '电路仿真。画图', guide_name: 'guide.pdf', attachment_names: [] },
    { id: 'second', created_at: '2026-11-09T00:00:00Z', requirements: '继续检查波形', guide_name: null, attachment_names: [] },
  ] })
  render(<App />)
  await screen.findByText('电路仿真。画图')
  await user.type(screen.getByRole('textbox', { name: '实验要求' }), '继续检查波形')
  await user.click(screen.getByRole('button', { name: '发送' }))

  await waitFor(() => expect(createTaskMock).toHaveBeenCalledOnce())
  const request = createTaskMock.mock.calls[0][0]
  expect(request.conversation_id).toBe('existing')
  expect(request.guide).toBeNull()
  expect(screen.getByRole('region', { name: '对话记录' })).toHaveTextContent('继续检查波形')
  expect(screen.getByRole('navigation', { name: '历史任务' }).querySelectorAll('.history-list__item')).toHaveLength(1)
})

test('shows messages from top to bottom with each reply after its request', async () => {
  getTasksMock.mockResolvedValue({ items: [{
    id: 'conversation', title: '仿真实验', created_at: '2026-11-08T00:00:00Z', messages: [
      { id: 'old', created_at: '2026-11-08T00:00:00Z', requirements: '先分析电路', guide_name: 'guide.pdf', attachment_names: [], reply: '先准备电路图', status: 'answered' },
      { id: 'new', created_at: '2026-11-09T00:00:00Z', requirements: '再检查波形', guide_name: null, attachment_names: [], reply: '请核对时序', status: 'answered' },
    ],
  }] })
  render(<App />)
  await screen.findByText('请核对时序')

  const bubbles = Array.from(document.querySelectorAll('.conversation__item'))
  expect(bubbles.map((bubble) => bubble.textContent)).toEqual([
    '先分析电路指导文件 · guide.pdf已回复', '先准备电路图', '再检查波形已回复', '请核对时序',
  ])
  expect(bubbles[0]).toHaveClass('conversation__item--user')
  expect(bubbles[1]).toHaveClass('conversation__item--assistant')
  expect(bubbles[2]).toHaveClass('conversation__item--user')
  expect(bubbles[3]).toHaveClass('conversation__item--assistant')
})

test('deletes a confirmed conversation and selects the next saved one', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  getTasksMock.mockResolvedValue({ items: [
    { id: 'first', title: '第一项实验', created_at: '2026-11-07T00:00:00Z', messages: [{ id: 'one', created_at: '2026-11-07T00:00:00Z', requirements: '第一项实验', guide_name: 'one.pdf', attachment_names: [] }] },
    { id: 'second', title: '第二项实验', created_at: '2026-11-08T00:00:00Z', messages: [{ id: 'two', created_at: '2026-11-08T00:00:00Z', requirements: '第二项实验', guide_name: 'two.pdf', attachment_names: [] }] },
  ] })
  render(<App />)
  await screen.findByText('第二项实验', { selector: '.conversation__text' })
  await user.click(screen.getByRole('button', { name: '归档会话 第二项实验' }))

  await waitFor(() => expect(deleteTaskMock).toHaveBeenCalledWith('second'))
  expect(confirm).toHaveBeenCalledOnce()
  expect(screen.queryByRole('button', { name: '第二项实验' })).not.toBeInTheDocument()
  expect(document.querySelector('.breadcrumb strong')).toHaveTextContent('第一项实验')
  confirm.mockRestore()
})

test('keeps the request in the composer when saving fails', async () => {
  createTaskMock.mockRejectedValueOnce(new Error('服务暂不可用'))
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('本地服务已连接')
  const guideInput = document.querySelector<HTMLInputElement>('.topbar__guide input[type="file"]')!
  fireEvent.change(guideInput, { target: { files: [new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })] } })
  await user.type(screen.getByRole('textbox', { name: '实验要求' }), '继续分析')
  await user.click(screen.getByRole('button', { name: '发送' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('服务暂不可用')
  expect(screen.getByRole('textbox', { name: '实验要求' })).toHaveValue('继续分析')
  expect(screen.getByRole('region', { name: '对话记录' })).toBeEmptyDOMElement()
  expect(document.querySelector('.page-wrap')?.firstElementChild).toBe(screen.getByRole('region', { name: '对话记录' }))
})

test('shows later task files below the first file and promotes one when removed', async () => {
  const user = userEvent.setup()
  render(<App />)
  const input = document.querySelector<HTMLInputElement>('.task-panel input[type="file"]')!
  const first = new File(['image'], 'first.png', { type: 'image/png' })
  const laterFiles = Array.from({ length: 5 }, (_, index) => new File(['%PDF-'], `attachment-${index + 1}.pdf`, { type: 'application/pdf' }))
  fireEvent.change(input, { target: { files: [first, ...laterFiles] } })

  expect(document.querySelector('.task-panel .dropzone__filename')).toHaveTextContent('first.png')
  expect(document.querySelectorAll('.task-panel .attachment-item')).toHaveLength(5)
  laterFiles.forEach((file) => expect(document.querySelector('.task-panel .attachment-list')).toHaveTextContent(file.name))
  await user.click(screen.getByRole('button', { name: '移除 first.png' }))
  expect(document.querySelector('.task-panel .dropzone__filename')).toHaveTextContent('attachment-1.pdf')
  expect(document.querySelectorAll('.task-panel .attachment-item')).toHaveLength(4)
})

test('rejects invalid or oversized files before submitting', async () => {
  render(<App />)
  await screen.findByRole('button', { name: '实验软件' })
  const guideInput = document.querySelector<HTMLInputElement>('.topbar__guide input[type="file"]')!
  const taskInput = document.querySelector<HTMLInputElement>('.task-panel input[type="file"]')!
  fireEvent.change(guideInput, { target: { files: [new File(['text'], 'guide.txt', { type: 'text/plain' })] } })
  expect(screen.getByRole('alert')).toHaveTextContent('任务指导书仅支持 PDF 文件。')

  const oversizedGuide = new File(['content'], 'large-guide.pdf', { type: 'application/pdf' })
  Object.defineProperty(oversizedGuide, 'size', { value: 120 * 1024 * 1024 + 1 })
  fireEvent.change(guideInput, { target: { files: [oversizedGuide] } })
  expect(screen.getByRole('alert')).toHaveTextContent('指导文件不能超过 120 MB。')

  const guideAtLimit = new File(['%PDF-'], 'guide-at-limit.pdf', { type: 'application/pdf' })
  Object.defineProperty(guideAtLimit, 'size', { value: 120 * 1024 * 1024 })
  fireEvent.change(guideInput, { target: { files: [guideAtLimit] } })
  expect(document.querySelector('.topbar__guide')).toHaveTextContent('guide-at-limit.pdf')

  const oversized = new File(['content'], 'large.pdf', { type: 'application/pdf' })
  Object.defineProperty(oversized, 'size', { value: 100 * 1024 * 1024 + 1 })
  fireEvent.change(taskInput, { target: { files: [oversized] } })
  expect(screen.getByRole('alert')).toHaveTextContent('PDF 附件不能超过 100 MB')
  expect(screen.queryByText('large.pdf')).not.toBeInTheDocument()
  expect(createTaskMock).not.toHaveBeenCalled()
})
