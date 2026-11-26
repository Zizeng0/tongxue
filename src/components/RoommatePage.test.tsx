import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { deleteRoommateChat, getRoommateChats, getRoommateImage, getTaskProgress, sendRoommateMessage } from '../api'
import type { Conversation, ConversationMessage } from '../contracts/responses/conversation'
import RoommatePage from './RoommatePage'
import '../styles.css'

vi.mock('../api', () => ({
  deleteRoommateChat: vi.fn(),
  getRoommateChats: vi.fn(),
  getRoommateImage: vi.fn(),
  getTaskProgress: vi.fn(),
  sendRoommateMessage: vi.fn(),
}))

const created_at = '2026-11-23T08:00:00Z'
const first: ConversationMessage = {
  id: 'first', created_at, requirements: '制作一份课程汇报 PPT', guide_name: null,
  attachment_names: ['题图.png', '条件.jpg'], reply: '演示文稿已生成。', artifact_files: ['演示文稿.pptx'],
}
const conversation: Conversation = { id: 'room-1', title: '系统函数题解', created_at, messages: [first] }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(getRoommateChats).mockResolvedValue({ items: [] })
  vi.mocked(getRoommateImage).mockRejectedValue(new Error('test image'))
  vi.mocked(getTaskProgress).mockResolvedValue({ stage: '正在查题库', done: false })
  vi.mocked(deleteRoommateChat).mockResolvedValue(undefined)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

test('sizes the input to the draft with room for four lines by default', async () => {
  render(<RoommatePage />)
  await screen.findByText('暂无历史对话')
  const prompt = screen.getByRole('textbox', { name: '给室友发消息' })
  const sizingBox = prompt.closest('.roommate__input')!
  expect(getComputedStyle(sizingBox).minHeight).toBe('max(95px, calc(5rem + 15px))')
  expect(sizingBox).toHaveAttribute('data-value', '')

  const draft = `${'自然换行的内容'.repeat(50)}\n下一行\n`
  fireEvent.change(prompt, { target: { value: draft } })
  expect(sizingBox).toHaveAttribute('data-value', draft)

  fireEvent.click(screen.getByRole('button', { name: '新对话' }))
  expect(prompt).toHaveValue('')
  expect(sizingBox).toHaveAttribute('data-value', '')
})

test('uploads a template with images, links to projects, and sends a manually entered request', async () => {
  vi.mocked(sendRoommateMessage).mockResolvedValueOnce(conversation).mockResolvedValueOnce({
    ...conversation,
    messages: [first, { id: 'second', created_at, requirements: '说明第二步', guide_name: null,
      attachment_names: [], reply: '第二步是整理传递函数。' }],
  })
  const user = userEvent.setup()
  const { container } = render(<RoommatePage />)
  await screen.findByText('暂无历史对话')
  const prompt = screen.getByRole('textbox', { name: '给室友发消息' })
  expect(prompt).toHaveAttribute('rows', '1')
  expect(screen.queryByRole('button', { name: /解题并导出/ })).not.toBeInTheDocument()
  const png = new File(['png'], '题图.png', { type: 'image/png' })
  const jpeg = new File(['jpeg'], '条件.jpg', { type: 'image/jpeg' })
  const template = new File(['pptx'], '模板.pptx')
  const pdf = new File(['pdf'], '课程资料.pdf', { type: 'application/pdf' })
  const upload = container.querySelector<HTMLInputElement>('input[type=file]')!
  await user.upload(upload, [png, jpeg, template, pdf])
  await user.type(prompt, first.requirements)
  await user.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText('演示文稿已生成。')
  const reply = screen.getByRole('article', { name: '助手回复' })
  expect(reply).toHaveClass('conversation__item--assistant')
  expect(within(reply).getByText('演示文稿已生成。').parentElement).toHaveClass('conversation__markdown')
  expect(sendRoommateMessage).toHaveBeenNthCalledWith(1, first.requirements, [png, jpeg], null, expect.any(String), [template, pdf])
  expect(screen.getByRole('link', { name: '在我的项目查看' })).toHaveAttribute('href', '#/projects/room-1')
  expect(screen.queryByRole('button', { name: /下载/ })).not.toBeInTheDocument()
  await user.type(prompt, '说明第二步')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText('第二步是整理传递函数。')
  expect(sendRoommateMessage).toHaveBeenNthCalledWith(2, '说明第二步', [], 'room-1', expect.any(String), [])
  await user.click(screen.getByRole('button', { name: '新对话' }))
  expect(screen.getByRole('heading', { name: '室友' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: conversation.title }))
  expect(screen.getByText('第二步是整理传递函数。')).toBeInTheDocument()
})

test('uploads a Word thesis draft with a PDF format guide', async () => {
  vi.mocked(sendRoommateMessage).mockResolvedValue({ ...conversation, messages: [{
    ...first, requirements: '按学校规范修改论文格式', attachment_names: [],
    reply: '论文排版已完成。', artifact_files: ['论文初稿-排版版.docx'],
  }] })
  const user = userEvent.setup()
  const { container } = render(<RoommatePage />)
  await screen.findByText('暂无历史对话')
  const draft = new File(['docx'], '论文初稿.docx')
  const guide = new File(['pdf'], '学校规范.pdf')
  await user.upload(container.querySelector<HTMLInputElement>('input[type=file]')!, [draft, guide])
  await user.type(screen.getByRole('textbox', { name: '给室友发消息' }), '按学校规范修改论文格式')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText('论文排版已完成。')
  expect(sendRoommateMessage).toHaveBeenCalledExactlyOnceWith('按学校规范修改论文格式', [], null, expect.any(String), [draft, guide])
  expect(screen.getByRole('link', { name: '在我的项目查看' })).toHaveAttribute('href', '#/projects/room-1')
})

test('shows processing progress and preserves the draft if sending fails', async () => {
  let fail!: (reason: Error) => void
  vi.mocked(sendRoommateMessage).mockImplementation(() => new Promise((_resolve, reject) => { fail = reject }))
  const user = userEvent.setup()
  const { container } = render(<RoommatePage />)
  await screen.findByText('暂无历史对话')
  const input = container.querySelector<HTMLInputElement>('input[type=file]')!
  fireEvent.change(input, { target: { files: [new File(['zip'], 'test.zip')] } })
  expect(screen.getByRole('alert')).toHaveTextContent('支持图片、DOCX、PDF、PPTX')
  const prompt = screen.getByRole('textbox', { name: '给室友发消息' })
  await user.type(prompt, '解答采样题')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('正在查题库'))
  expect(screen.getByRole('article', { name: '助手回复' })).toHaveClass('conversation__item--assistant')
  fail(new Error('服务暂不可用'))
  expect(await screen.findByRole('alert')).toHaveTextContent('服务暂不可用')
  expect(prompt).toHaveValue('解答采样题')
})

test('sends on Enter after typing, allows a Shift+Enter newline, and ignores composition Enter', async () => {
  vi.mocked(sendRoommateMessage).mockResolvedValue(conversation)
  const user = userEvent.setup()
  render(<RoommatePage />)
  await screen.findByText('暂无历史对话')
  const prompt = screen.getByRole('textbox', { name: '给室友发消息' })
  await user.type(prompt, '第一页')
  await user.keyboard('{Shift>}{Enter}{/Shift}讲义要点')
  expect(prompt).toHaveValue('第一页\n讲义要点')
  fireEvent.keyDown(prompt, { key: 'Enter', isComposing: true })
  expect(sendRoommateMessage).not.toHaveBeenCalled()
  await user.keyboard('{Enter}')
  expect(sendRoommateMessage).toHaveBeenCalledExactlyOnceWith('第一页\n讲义要点', [], null, expect.any(String), [])
  await screen.findByText('演示文稿已生成。')
})

test('shows live stages and elapsed time, then uses the recorded time on the reply', async () => {
  let finish!: (result: Conversation) => void
  vi.mocked(sendRoommateMessage).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  vi.mocked(getTaskProgress).mockResolvedValueOnce({ stage: '正在读取 PDF 素材', done: false })
    .mockResolvedValue({ stage: '正在编排幻灯片', done: false })
  render(<RoommatePage />)
  await screen.findByText('暂无历史对话')
  vi.useFakeTimers()
  try {
    const log = within(screen.getByRole('log', { name: '室友消息' }))
    fireEvent.change(screen.getByRole('textbox', { name: '给室友发消息' }), { target: { value: first.requirements } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(log.getByRole('status')).toHaveTextContent('正在提交任务')
    expect(log.getByText('已处理 0秒')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(400) })
    expect(log.getByRole('status')).toHaveTextContent('正在读取 PDF 素材')
    await act(async () => { await vi.advanceTimersByTimeAsync(1750) })
    expect(log.getByRole('status')).toHaveTextContent('正在编排幻灯片')
    expect(log.getByText('已处理 2秒')).toBeInTheDocument()
    await act(async () => { finish({ ...conversation, messages: [{ ...first, processing_seconds: 4.5 }] }) })
    expect(log.queryByRole('status')).not.toBeInTheDocument()
    expect(log.getByText('已处理 4秒')).toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('uses the shared upload rail to add and remove attachments in place', async () => {
  const { container } = render(<RoommatePage />)
  const upload = within(screen.getByRole('group', { name: '任务附件' }))
  const input = container.querySelector<HTMLInputElement>('input[type=file]')!
  const pick = vi.spyOn(input, 'click')
  fireEvent.click(upload.getByRole('button', { name: '添加附件' }))
  const pdf = new File(['pdf'], '讲义.pdf')
  const png = new File(['png'], '照片.png')
  fireEvent.change(input, { target: { files: [pdf, png] } })
  fireEvent.click(upload.getByRole('button', { name: '添加文件' }))
  fireEvent.change(input, { target: { files: [new File(['txt'], '笔记.txt')] } })
  expect(pick).toHaveBeenCalledTimes(2)
  const rail = input.closest('.dropzone--multiple')!.querySelector('.dropzone__files')!
  expect([...rail.querySelectorAll('.dropzone__filename')].map((file) => file.textContent)).toEqual(['讲义.pdf', '照片.png', '笔记.txt'])
  expect(getComputedStyle(rail).overflowX).toBe('auto')
  fireEvent.click(upload.getByRole('button', { name: '移除 照片.png' }))
  vi.mocked(sendRoommateMessage).mockResolvedValue(conversation)
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText('演示文稿已生成。')
  expect(sendRoommateMessage).toHaveBeenCalledWith('', [], null, expect.any(String), [pdf, expect.objectContaining({ name: '笔记.txt' })])
})

test('archives only the confirmed history entry', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
  vi.mocked(getRoommateChats).mockResolvedValue({ items: [conversation, { ...conversation, id: 'room-2', title: '课程安排' }] })
  render(<RoommatePage />)
  fireEvent.click(await screen.findByRole('button', { name: conversation.title }))
  const remove = screen.getByRole('button', { name: `删除会话 ${conversation.title}` })
  fireEvent.click(remove)
  expect(deleteRoommateChat).not.toHaveBeenCalled()
  fireEvent.click(remove)
  await screen.findByRole('heading', { name: '室友' })
  expect(confirm).toHaveBeenCalledTimes(2)
  expect(deleteRoommateChat).toHaveBeenCalledExactlyOnceWith('room-1')
  expect(screen.queryByRole('button', { name: conversation.title })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '课程安排' })).toBeInTheDocument()
})

test('keeps independent progress and merges replies when tasks finish out of order', async () => {
  const finish = new Map<string, (chat: Conversation) => void>()
  vi.mocked(sendRoommateMessage).mockImplementation((question) => new Promise((resolve) => { finish.set(question, resolve) }))
  vi.mocked(getTaskProgress).mockResolvedValue({ stage: '处理中', conversation_id: 'room-1', done: false })
  render(<RoommatePage />)
  await screen.findByText('暂无历史对话')
  const send = (question: string) => {
    fireEvent.change(screen.getByRole('textbox', { name: '给室友发消息' }), { target: { value: question } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
  }
  send('任务甲')
  await screen.findByText('处理中')
  send('任务乙')
  expect(sendRoommateMessage).toHaveBeenNthCalledWith(2, '任务乙', [], 'room-1', expect.any(String), [])
  expect(screen.getAllByRole('status')).toHaveLength(2)
  fireEvent.click(screen.getByRole('button', { name: '新对话' }))
  send('任务丙')
  expect(sendRoommateMessage).toHaveBeenNthCalledWith(3, '任务丙', [], null, expect.any(String), [])
  const message = (id: string, requirements: string, reply: string, seconds: number) => ({
    ...first, id, requirements, reply, attachment_names: [], artifact_files: [], created_at: `2026-11-23T08:00:0${seconds}Z`,
  })
  const alpha = message('a', '任务甲', '答复甲', 1)
  const beta = message('b', '任务乙', '答复乙', 2)
  await act(async () => finish.get('任务乙')!({ ...conversation, title: '任务甲', messages: [beta] }))
  expect(screen.getByRole('button', { name: '任务丙' })).toHaveAttribute('aria-pressed', 'true')
  await act(async () => finish.get('任务甲')!({ ...conversation, title: '任务甲', messages: [alpha] }))
  await act(async () => finish.get('任务丙')!({ ...conversation, id: 'room-2', title: '任务丙',
    messages: [message('c', '任务丙', '答复丙', 3)] }))
  expect(screen.getByText('答复丙')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '任务甲' }))
  expect(screen.getAllByRole('article', { name: '助手回复' }).map((item) => item.textContent)).toEqual(['答复甲', '答复乙'])
})
