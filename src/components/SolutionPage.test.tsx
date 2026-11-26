import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { createSolution, deleteSolution, getSolutionImage, getSolutionInputImage, getSolutions } from '../api'
import SolutionPage from './SolutionPage'
import '../styles.css'

vi.mock('../api', () => ({ createSolution: vi.fn(), deleteSolution: vi.fn(), getSolutionImage: vi.fn(), getSolutionInputImage: vi.fn(), getSolutions: vi.fn() }))

const getSolutionsMock = vi.mocked(getSolutions)
const createSolutionMock = vi.mocked(createSolution)

test('renders each authorized solution image before the next question', async () => {
  const first = 'solutions/m1/first.png'
  const second = 'solutions/m1/second.png'
  vi.mocked(getSolutionImage).mockResolvedValue(new Blob(['png'], { type: 'image/png' }))
  vi.mocked(getSolutions).mockResolvedValue({ items: [{
    id: 'solution-1', title: '两题', created_at: '2026-11-19T00:00:00Z',
    messages: [{
      id: 'm1', created_at: '2026-11-19T00:00:00Z', requirements: '请画两题的图', guide_name: null,
      attachment_names: [], output_images: [second, first],
      reply: '## 第 2 题\n\n推导甲\n\n![图甲](solutions/m1/first.png)\n\n## 第 3 题\n\n推导乙\n\n![图乙](solutions/m1/second.png)\n\n![其他](https://example.com/photo.png)',
    }],
  }] })
  render(<SolutionPage />)
  const pictures = await screen.findAllByRole('img', { name: '题解生成图' })
  const nextQuestion = screen.getByRole('heading', { name: '第 3 题' })
  expect(pictures).toHaveLength(2)
  expect(pictures[0].compareDocumentPosition(nextQuestion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(nextQuestion.compareDocumentPosition(pictures[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(getSolutionImage).toHaveBeenCalledTimes(2)
})
const deleteSolutionMock = vi.mocked(deleteSolution)

afterEach(cleanup)
beforeEach(() => {
  vi.resetAllMocks()
  if (!URL.createObjectURL) URL.createObjectURL = vi.fn().mockReturnValue('blob:question-image')
  if (!URL.revokeObjectURL) URL.revokeObjectURL = vi.fn()
  getSolutionsMock.mockResolvedValue({ items: [] })
  deleteSolutionMock.mockResolvedValue(undefined)
  vi.mocked(getSolutionInputImage).mockResolvedValue(new Blob(['image'], { type: 'image/png' }))
})

test('shows only the problem image, text input, reply area and solution history', async () => {
  render(<SolutionPage />)
  expect(screen.getByRole('region', { name: '解题对话' })).toBeEmptyDOMElement()
  expect(screen.getByRole('button', { name: '上传题目图片' })).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: '题目描述' })).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '解题历史' })).not.toBeInTheDocument()
  expect(await screen.findByText('暂无历史对话')).toBeInTheDocument()
  expect(screen.queryByText('交付成果')).not.toBeInTheDocument()
  expect(screen.queryByText('实验软件')).not.toBeInTheDocument()
  expect(document.querySelectorAll('input[type="file"]')).toHaveLength(1)
})

test('submits an image and question and displays the model reply', async () => {
  const user = userEvent.setup()
  createSolutionMock.mockResolvedValue({ id: 'solution-1', title: '解释采样定理', created_at: '2026-11-19T00:00:00Z', messages: [{
    id: 'message-1', created_at: '2026-11-19T00:00:00Z', requirements: '解释采样定理', guide_name: null,
    attachment_names: ['problem.png'], reply: '采样频率应高于信号最高频率的两倍。', status: 'answered',
  }] })
  const { container } = render(<SolutionPage />)
  const image = new File(['image'], 'problem.png', { type: 'image/png' })
  await user.upload(container.querySelector('input[type="file"]')!, image)
  await user.type(screen.getByRole('textbox', { name: '题目描述' }), '解释采样定理')
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(createSolutionMock).toHaveBeenCalledWith('解释采样定理', image, null, expect.any(String))
  expect(await screen.findByText('采样频率应高于信号最高频率的两倍。')).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '解题历史' })).toHaveTextContent('解释采样定理')
  expect(screen.queryByRole('button', { name: /下载/ })).not.toBeInTheDocument()
})

test('keeps failed input and supports image-only questions', async () => {
  const user = userEvent.setup()
  createSolutionMock.mockRejectedValueOnce(new Error('教材知识库暂不可用'))
  const { container } = render(<SolutionPage />)
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(screen.getByRole('alert')).toHaveTextContent('请上传题目图片或输入题目')
  const image = new File(['image'], 'question.jpg', { type: 'image/jpeg' })
  await user.upload(container.querySelector('input[type="file"]')!, image)
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(createSolutionMock).toHaveBeenCalledWith('', image, null, expect.any(String))
  expect(await screen.findByRole('alert')).toHaveTextContent('教材知识库暂不可用')
  expect(container.querySelector('.dropzone__filename')).toHaveTextContent('question.jpg')
  await waitFor(() => expect(screen.getByRole('region', { name: '解题对话' })).toBeEmptyDOMElement())
})

test('loads, switches and deletes only solution conversations', async () => {
  const user = userEvent.setup()
  getSolutionsMock.mockResolvedValue({ items: [{ id: 'old', title: '旧题', created_at: '2026-11-18T00:00:00Z', messages: [{
    id: 'old-message', created_at: '2026-11-18T00:00:00Z', requirements: '旧题内容', guide_name: null,
    attachment_names: [], reply: '旧题答案', status: 'answered',
  }] }] })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<SolutionPage />)
  expect(await screen.findByText('旧题答案')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '新增会话' }))
  expect(screen.getByRole('region', { name: '解题对话' })).toBeEmptyDOMElement()
  await user.click(screen.getByRole('button', { name: '旧题' }))
  expect(screen.getByText('旧题答案')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '归档会话 旧题' }))
  await waitFor(() => expect(deleteSolutionMock).toHaveBeenCalledWith('old'))
  expect(await screen.findByText('暂无历史对话')).toBeInTheDocument()
  vi.restoreAllMocks()
})

test('refreshes a conversation after returning and shows a later reply', async () => {
  const message = { id: 'm1', created_at: '2026-11-21T00:00:00Z', requirements: '求解新题',
    guide_name: null, attachment_names: [], status: 'processing' as const }
  getSolutionsMock.mockResolvedValueOnce({ items: [{ id: 'new', title: '求解新题',
    created_at: '2026-11-21T00:00:00Z', messages: [message] }] })
  getSolutionsMock.mockResolvedValue({ items: [{ id: 'new', title: '求解新题',
    created_at: '2026-11-21T00:00:00Z', messages: [{ ...message, status: 'answered', reply: '已完成的答案' }] }] })
  render(<SolutionPage />)
  expect(await screen.findByText('正在识别题目并检索教材…')).toBeInTheDocument()
  expect(await screen.findByText('已完成的答案', {}, { timeout: 4500 })).toBeInTheDocument()
})

test('restores a follow-up message after leaving the page during submission', async () => {
  const user = userEvent.setup()
  const original = { id: 'm1', created_at: '2026-11-21T00:00:00Z', requirements: '原题',
    guide_name: null, attachment_names: [], status: 'answered' as const, reply: '原答案' }
  const conversation = { id: 'existing', title: '原题', created_at: '2026-11-21T00:00:00Z', messages: [original] }
  getSolutionsMock.mockResolvedValue({ items: [conversation] })
  createSolutionMock.mockImplementation(() => new Promise(() => {}))
  const page = render(<SolutionPage />)
  expect(await screen.findByText('原答案')).toBeInTheDocument()
  await user.type(screen.getByRole('textbox', { name: '题目描述' }), '继续求解')
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(createSolutionMock).toHaveBeenCalledWith('继续求解', null, 'existing', expect.any(String))
  expect(screen.getByText('继续求解')).toBeInTheDocument()
  page.unmount()

  getSolutionsMock.mockResolvedValue({ items: [{ ...conversation, messages: [original, {
    id: 'm2', created_at: '2026-11-21T00:01:00Z', requirements: '继续求解', guide_name: null,
    attachment_names: [], status: 'processing' as const,
  }] }] })
  render(<SolutionPage />)
  expect(await screen.findByText('继续求解')).toBeInTheDocument()
  expect(screen.getByText('正在解题')).toBeInTheDocument()
  expect(screen.getByText('正在识别题目并检索教材…')).toBeInTheDocument()
  getSolutionsMock.mockResolvedValue({ items: [{ ...conversation, messages: [original, {
    id: 'm2', created_at: '2026-11-21T00:01:00Z', requirements: '继续求解', guide_name: null,
    attachment_names: [], status: 'answered' as const, reply: '追问答案',
  }] }] })
  expect(await screen.findByText('追问答案', {}, { timeout: 4500 })).toBeInTheDocument()
})

test('loads a generated solution image into the reply', async () => {
  const createObjectURL = vi.fn().mockReturnValue('blob:solution-image')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
  vi.mocked(getSolutionImage).mockResolvedValue(new Blob(['png'], { type: 'image/png' }))
  getSolutionsMock.mockResolvedValue({ items: [{ id: 'solution-1', title: '波形', created_at: '2026-11-18T00:00:00Z', messages: [{
    id: 'm1', created_at: '2026-11-18T00:00:00Z', requirements: '画波形', guide_name: null, attachment_names: [],
    reply: '波形见图', status: 'answered', output_images: ['solutions/m1/function.png'],
  }] }] })
  const { unmount } = render(<SolutionPage />)
  expect(await screen.findByRole('img', { name: '题解生成图' })).toHaveAttribute('src', 'blob:solution-image')
  expect(getSolutionImage).toHaveBeenCalledWith('solution-1', 'solutions/m1/function.png')
  unmount()
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:solution-image')
  vi.unstubAllGlobals()
})

test('shows the uploaded image beside the user message and restores it from history', async () => {
  const createObjectURL = vi.fn().mockReturnValue('blob:question-image')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
  getSolutionsMock.mockResolvedValue({ items: [{ id: 'solution-1', title: '题目', created_at: '2026-11-19T00:00:00Z', messages: [{
    id: 'm1', created_at: '2026-11-19T00:00:00Z', requirements: '求解', guide_name: null,
    attachment_names: ['problem.png'], reply: '答案', status: 'answered',
  }] }] })
  const { unmount } = render(<SolutionPage />)
  expect(await screen.findByRole('img', { name: '上传的题目图片' })).toHaveAttribute('src', 'blob:question-image')
  expect(getSolutionInputImage).toHaveBeenCalledWith('solution-1', 'm1')
  unmount()
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:question-image')
  vi.unstubAllGlobals()
})

test('previews the uploaded image while the solution is processing', async () => {
  const user = userEvent.setup()
  const createObjectURL = vi.fn().mockReturnValue('blob:pending-image')
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL: vi.fn() })
  createSolutionMock.mockImplementation(() => new Promise(() => {}))
  const { container } = render(<SolutionPage />)
  const file = new File(['image'], 'problem.png', { type: 'image/png' })
  await user.upload(container.querySelector('input[type="file"]')!, file)
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(await screen.findByRole('img', { name: '上传的题目图片' })).toHaveAttribute('src', 'blob:pending-image')
  expect(createObjectURL).toHaveBeenCalledWith(file)
  expect(getSolutionInputImage).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
})

test('renders mathematical Markdown and saved processing time', async () => {
  getSolutionsMock.mockResolvedValue({ items: [{ id: 'math', title: '傅里叶变换', created_at: '2026-11-19T00:00:00Z', messages: [{
    id: 'm1', created_at: '2026-11-19T00:00:00Z', requirements: '求变换', guide_name: null, attachment_names: [],
    reply: '公式：\n\n$$X(e^{j\\omega}) = \\sum_n x(n)e^{-j\\omega n}$$\n\n**结论**', status: 'answered', processing_seconds: 70,
  }] }] })
  const { container } = render(<SolutionPage />)
  expect(await screen.findByText('已处理 1分10秒')).toBeInTheDocument()
  expect(container.querySelector('.katex-display')).toBeInTheDocument()
  expect(container.querySelector('strong')).toHaveTextContent('结论')
  expect(screen.queryByText(/\$\$X/)).not.toBeInTheDocument()
})

test('renders absolute-value bars inside a table without splitting math cells', async () => {
  getSolutionsMock.mockResolvedValue({ items: [{ id: 'math', title: '频率响应', created_at: '2026-11-19T00:00:00Z', messages: [{
    id: 'm1', created_at: '2026-11-19T00:00:00Z', requirements: '求频响', guide_name: null, attachment_names: [],
    reply: '| 频率 | 幅值 |\n|---|---|\n| $\\omega=0$ | $|H(e^{j\\omega})|=19$ |\n\n\\[H(e^{j\\omega})=\\frac{1+0.9e^{-j\\omega}}{1-0.9e^{-j\\omega}}\\]', status: 'answered',
  }] }] })
  const { container } = render(<SolutionPage />)
  await screen.findByText('频率响应')
  expect(container.querySelectorAll('tbody tr td')).toHaveLength(2)
  expect(container.querySelectorAll('.katex-display')).toHaveLength(1)
  expect(container.querySelector('tbody tr td:last-child .katex')).toBeInTheDocument()
})

test('renders multiline dollar formulas without red KaTeX errors or swallowed headings', async () => {
  getSolutionsMock.mockResolvedValue({ items: [{ id: 'math', title: '极零点', created_at: '2026-11-19T00:00:00Z', messages: [{
    id: 'm1', created_at: '2026-11-19T00:00:00Z', requirements: '求极点', guide_name: null, attachment_names: [],
    reply: '## 零点\n\n$$z=\\frac{-5\\pm15}{2}\n\\Longrightarrow\nz_1=5,\\quad z_2=-10$$\n\n$$\\boxed{z_1=5}$$\n\n### 极点\n\n$|p|<1$ 时稳定。', status: 'answered',
  }] }] })
  const { container } = render(<SolutionPage />)
  expect(await screen.findByRole('heading', { name: '极点' })).toBeInTheDocument()
  expect(container.querySelectorAll('.katex-display')).toHaveLength(2)
  expect(container.querySelector('.katex-error')).not.toBeInTheDocument()
  expect(container.querySelector('.conversation__markdown')).not.toHaveTextContent('$$')
})

test('updates elapsed time while a solution is running', async () => {
  const user = userEvent.setup()
  let complete!: (value: Awaited<ReturnType<typeof createSolution>>) => void
  createSolutionMock.mockImplementation(() => new Promise((resolve) => { complete = resolve }))
  render(<SolutionPage />)
  await user.type(screen.getByRole('textbox', { name: '题目描述' }), '求傅里叶变换')
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(screen.getByText('已处理 0秒')).toBeInTheDocument()
  expect(await screen.findByText('已处理 1秒', {}, { timeout: 2200 })).toBeInTheDocument()
  complete({ id: 'math', title: '求傅里叶变换', created_at: '2026-11-19T00:00:00Z', messages: [{
    id: 'm1', created_at: '2026-11-19T00:00:00Z', requirements: '求傅里叶变换', guide_name: null,
    attachment_names: [], reply: '完成', status: 'answered', processing_seconds: 1.3,
  }] })
  expect(await screen.findByText('完成')).toBeInTheDocument()
  expect(screen.getByText('已处理 1秒')).toBeInTheDocument()
})

test('pending candidates appear once during the poll and the finished reply keeps the vision status', async () => {
  vi.useFakeTimers()
  try {
    const createdAt = new Date().toISOString()
    let complete!: (value: Awaited<ReturnType<typeof createSolution>>) => void
    let draft!: Awaited<ReturnType<typeof createSolution>>
    createSolutionMock.mockImplementation((question, _image, _id, requestId) => {
      draft = { id: 'saved', title: question, created_at: createdAt, messages: [{
        id: 'm1', created_at: createdAt, requirements: question, request_id: requestId,
        guide_name: null, attachment_names: [], status: 'processing',
        exercise_candidates: '### 相似候选\n\n- 候选甲\n- 候选乙\n- 候选丙',
      }] }
      return new Promise((resolve) => { complete = resolve })
    })
    render(<SolutionPage />)
    await act(async () => {})
    fireEvent.change(screen.getByRole('textbox', { name: '题目描述' }), { target: { value: '新题' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    getSolutionsMock.mockResolvedValue({ items: [draft] })
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    const history = within(screen.getByRole('navigation', { name: '解题历史' }))
    expect(history.getAllByRole('button', { name: '新题' })).toHaveLength(1)
    expect(screen.getByText('已处理 3秒')).toBeInTheDocument()
    const candidates = screen.getByRole('region', { name: '相似题目' })
    expect(within(candidates).getAllByRole('listitem')).toHaveLength(3)
    expect(screen.getByText('候选待核对，正在继续处理题图…')).toBeInTheDocument()

    const final = { ...draft, messages: [{ ...draft.messages[0], status: 'answered' as const,
      reply: '最终答复', processing_seconds: 3.2, vision_status: '视觉 API 拒绝认证（HTTP 401）。本地 Ollama' }] }
    await act(async () => { complete(final) })
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(history.getAllByRole('button', { name: '新题' })).toHaveLength(1)
    expect(screen.getByText('最终答复')).toBeInTheDocument()
    expect(screen.getByText(/HTTP 401/)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '相似题目' })).toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

test('unchanged polling keeps the same inline image and scroll position', async () => {
  vi.useFakeTimers()
  const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:stable-figure')
  try {
    const conversation = { id: 'saved', title: '波形', created_at: '2026-11-19T00:00:00Z', messages: [{
      id: 'm1', created_at: '2026-11-19T00:00:00Z', requirements: '请求',
      guide_name: null, attachment_names: [], status: 'answered' as const,
      output_images: ['solutions/m1/figure.png'], reply: '![波形](solutions/m1/figure.png)',
    }] }
    vi.mocked(getSolutionImage).mockResolvedValue(new Blob(['png'], { type: 'image/png' }))
    getSolutionsMock.mockImplementation(async () => ({ items: [structuredClone(conversation)] }))
    render(<SolutionPage />)
    await act(async () => {})
    const panel = screen.getByRole('region', { name: '解题对话' })
    Object.defineProperties(panel, { scrollHeight: { value: 1000 }, clientHeight: { value: 500 } })
    panel.scrollTop = 100
    fireEvent.scroll(panel)
    const figure = screen.getByRole('img', { name: '题解生成图' })
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(screen.getByRole('img', { name: '题解生成图' })).toBe(figure)
    expect(getSolutionImage).toHaveBeenCalledTimes(1)
    expect(panel.scrollTop).toBe(100)
  } finally {
    createObjectURL.mockRestore()
    vi.useRealTimers()
  }
})

test('brings the explanation into view above a long reply with figures', async () => {
  const bounds = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    return { top: this.classList.contains('conversation__assistant-wrap') ? 240 : 40 } as DOMRect
  })
  try {
    vi.mocked(getSolutionImage).mockRejectedValue(new Error('Fixture image is not available'))
    getSolutionsMock.mockResolvedValue({ items: [{ id: 'saved', title: '波形', created_at: '2026-11-19T00:00:00Z', messages: [{
      id: 'm1', created_at: '2026-11-19T00:00:00Z', requirements: '计算卷积', guide_name: null,
      attachment_names: [], status: 'answered', output_images: ['solutions/m1/figure.png'],
      reply: '## 卷积推导\n\n先计算线性卷积。\n\n![波形](solutions/m1/figure.png)',
    }] }] })
    render(<SolutionPage />)
    const panel = screen.getByRole('region', { name: '解题对话' })
    Object.defineProperty(panel, 'scrollHeight', { value: 1500 })
    expect(await screen.findByRole('heading', { name: '卷积推导' })).toBeInTheDocument()
    await waitFor(() => expect(panel.scrollTop).toBe(200))
  } finally {
    bounds.mockRestore()
  }
})
