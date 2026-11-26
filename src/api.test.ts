import { afterEach, expect, test, vi } from 'vitest'
import { clearToken, createSolution, createTask, deleteSolution, deleteTask, downloadReport, getCurrentUser, getSolutionImage, getTaskProgress, saveToken, uploadAvatar } from './api'

afterEach(() => { vi.unstubAllGlobals(); clearToken() })

test('serializes conversation fields and files as multipart form data', async () => {
  const guide = new File(['%PDF-1.7'], 'guide.pdf', { type: 'application/pdf' })
  const image = new File(['image'], 'figure.png', { type: 'image/png' })
  const experimentImage = new File(['image'], 'sample.png', { type: 'image/png' })
  const reportTemplate = new File(['template'], 'report.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
  const conversation = { id: 'saved', title: '分析波形', created_at: '2026-11-10', messages: [] }
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => conversation })
  vi.stubGlobal('fetch', fetchMock)

  expect(await createTask({
    requirements: '分析波形', software_id: 'modelsim', deliverables: ['report', 'code'],
    conversation_id: 'earlier', guide, report_template: reportTemplate, attachments: [image], other_files: [experimentImage], request_id: 'request-1',
  })).toEqual(conversation)

  expect(fetchMock).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({ method: 'POST' }))
  const form = fetchMock.mock.calls[0][1].body as FormData
  expect(form.get('requirements')).toBe('分析波形')
  expect(form.get('software_id')).toBe('modelsim')
  expect(form.getAll('deliverables')).toEqual(['report', 'code'])
  expect(form.get('conversation_id')).toBe('earlier')
  expect(form.get('guide')).toBe(guide)
  expect(form.get('report_template')).toBe(reportTemplate)
  expect(form.get('request_id')).toBe('request-1')
  expect(form.get('attachments')).toBe(image)
  expect(form.get('other_files')).toBe(experimentImage)
})

test('sends a solution image and question without experiment deliverables', async () => {
  const image = new File(['image'], 'problem.png', { type: 'image/png' })
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: true, status: 201, json: async () => ({ id: 'answer', messages: [] }) })
    .mockResolvedValueOnce({ ok: true, status: 204 })
  vi.stubGlobal('fetch', fetchMock)
  await createSolution('求解采样频率', image, 'previous')
  const [url, options] = fetchMock.mock.calls[0]
  expect(url).toBe('/api/solutions')
  const body = options.body as FormData
  expect(body.get('question')).toBe('求解采样频率')
  expect(body.get('image')).toBe(image)
  expect(body.get('conversation_id')).toBe('previous')
  expect(body.has('deliverables')).toBe(false)
  await deleteSolution('previous')
  expect(fetchMock.mock.calls[1][0]).toBe('/api/solutions/previous')
})

test('loads a generated solution image with the bearer token', async () => {
  saveToken('image-token')
  const image = new Blob(['png'], { type: 'image/png' })
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: async () => image })
  vi.stubGlobal('fetch', fetchMock)
  expect(await getSolutionImage('solution 1', 'solutions/message 1/function.png')).toBe(image)
  expect(fetchMock.mock.calls[0][0]).toBe('/api/solutions/solution%201/images/message%201/function.png')
  expect((fetchMock.mock.calls[0][1].headers as Headers).get('Authorization')).toBe('Bearer image-token')
  await expect(getSolutionImage('solution 1', '../secret.png')).rejects.toThrow('图片路径无效')
})

test('polls task progress with the bearer token', async () => {
  saveToken('sample-token')
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ stage: '模型正在分析任务', done: false }) })
  vi.stubGlobal('fetch', fetchMock)
  await expect(getTaskProgress('request id')).resolves.toEqual({ stage: '模型正在分析任务', done: false })
  expect(fetchMock).toHaveBeenCalledWith('/api/task-progress/request%20id', expect.objectContaining({
    headers: expect.any(Headers),
  }))
  expect((fetchMock.mock.calls[0][1].headers as Headers).get('Authorization')).toBe('Bearer sample-token')
})

test('deletes a conversation without parsing an empty response body', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 })
  vi.stubGlobal('fetch', fetchMock)

  await expect(deleteTask('id/with slash')).resolves.toBeUndefined()
  expect(fetchMock).toHaveBeenCalledWith('/api/tasks/id%2Fwith%20slash', expect.objectContaining({ method: 'DELETE' }))
})

test('sends bearer token and clears it when rejected', async () => {
  saveToken('sample-token')
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({ detail: '登录已失效' }) })
  vi.stubGlobal('fetch', fetchMock)
  await expect(getCurrentUser()).rejects.toThrow('登录已失效')
  expect((fetchMock.mock.calls[0][1].headers as Headers).get('Authorization')).toBe('Bearer sample-token')
  expect(window.localStorage.getItem('tongxue_access_token')).toBeNull()
})

test('uploads an avatar as authenticated multipart data', async () => {
  saveToken('sample-token')
  const response = { id: 1, user_name: 'alice', identity: 0, status: 0, image: '/api/users/avatars/example.png' }
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => response })
  vi.stubGlobal('fetch', fetchMock)
  const avatar = new File(['image'], 'avatar.png', { type: 'image/png' })
  expect(await uploadAvatar(avatar)).toEqual(response)
  const options = fetchMock.mock.calls[0][1]
  expect(options.body.get('avatar')).toBe(avatar)
  expect(options.headers.get('Authorization')).toBe('Bearer sample-token')
  expect(options.headers.has('Content-Type')).toBe(false)
})

test('downloads a report with its conversation and message IDs and bearer token', async () => {
  saveToken('sample-token')
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['report']) })
  const createObjectURL = vi.fn().mockReturnValue('blob:report')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

  await downloadReport('task id', 'message id', 'report.docx')

  expect(fetchMock).toHaveBeenCalledWith('/api/tasks/task%20id/reports/message%20id/report.docx', {
    headers: { Authorization: 'Bearer sample-token' },
  })
  expect(createObjectURL).toHaveBeenCalledOnce()
  expect(click).toHaveBeenCalledOnce()
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:report')
  click.mockRestore()
})
