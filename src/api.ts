import { requestText } from './constants'
import { conversationForm, type ConversationRequest } from './contracts/requests/conversation'
import type { Conversation, TaskHistoryResponse } from './contracts/responses/conversation'
import type { SoftwareResponse } from './types'

export type UserInfo = { id: number; user_name: string; identity: number; status: number; image: string; create_time?: string | null; update_time?: string | null; remark?: string | null }
export type UserUpdate = { user_name: string; user_password?: string; status?: number; identity?: number; image?: string; remark?: string }
export type UserPage = { items: UserInfo[]; total: number; page: number; page_size: number }
export type ProjectFile = { name: string; path: string; size: number; kind?: 'report' | 'paper' | 'code' | 'image' | 'presentation' }
export type ProjectSummary = { id: string; title: string; created_at: string; software_id: string; kind?: 'task' | 'solution' | 'roommate' }
export type ProjectDetail = ProjectSummary & { guide: ProjectFile | null; messages: { message_id: string; created_at: string; input_preview: string; inputs: ProjectFile[]; outputs: ProjectFile[] }[] }
export type McpPaper = { doi: string; title: string; authors: string; year: number | null; journal: string; abstract: string; url: string }
export type McpPdfResult = { pages: { page: number; text: string; source: 'native' | 'no_text_layer' }[]; truncated: boolean }
type AuthResponse = { access_token: string; token_type: string; user: UserInfo }
const tokenKey = 'tongxue_access_token'

export function readToken(): string | null { return window.localStorage.getItem(tokenKey) }
export function saveToken(token: string): void { window.localStorage.setItem(tokenKey, token) }
export function clearToken(): void { window.localStorage.removeItem(tokenKey) }

// 双击 index.html 时直连本地服务；开发服务器下由 Vite 代理 /api。
const apiBase = window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : ''
export function assetUrl(path: string): string { return path.startsWith('/api/') ? `${apiBase}${path}` : path }

async function requestResponse(path: string, options?: RequestInit): Promise<Response> {
  const headers = new Headers(options?.headers)
  const token = readToken()
  if (token && !path.startsWith('/api/auth/')) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${apiBase}${path}`, { ...options, headers })
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      clearToken()
      window.dispatchEvent(new Event('tongxue-auth-expired'))
    }
    // 优先展示后端校验信息，非 JSON 错误则回退到 HTTP 状态码。
    const error = await response.json().catch(() => null)
    throw new Error(typeof error?.detail === 'string' ? error.detail : requestText.failed(response.status))
  }
  return response
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await requestResponse(path, options)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function login(user_name: string, user_password: string): Promise<AuthResponse> {
  return request('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_name, user_password }) })
}

export function register(user_name: string, user_password: string, confirm_password: string): Promise<AuthResponse> {
  return request('/api/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_name, user_password, confirm_password }) })
}

export function getCurrentUser(): Promise<UserInfo> { return request('/api/users/me') }
export function uploadAvatar(avatar: File): Promise<UserInfo> {
  const body = new FormData()
  body.append('avatar', avatar)
  return request('/api/users/me/avatar', { method: 'POST', body })
}
export function getUsers(): Promise<UserInfo[]> { return request('/api/users') }
export function searchUsers(name: string, page: number, pageSize = 10): Promise<UserPage> {
  return request(`/api/users/search?name=${encodeURIComponent(name)}&page=${page}&page_size=${pageSize}`)
}
export function updateMe(update: UserUpdate): Promise<UserInfo> {
  return request('/api/users/me', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(update) })
}
export function updateUser(id: number, update: UserUpdate): Promise<UserInfo> {
  return request(`/api/users/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(update) })
}
export function deleteUser(id: number): Promise<void> { return request(`/api/users/${id}`, { method: 'DELETE' }) }

export function getSoftware(): Promise<SoftwareResponse> {
  return request<SoftwareResponse>('/api/software')
}

export function selectSoftware(softwareId: string): Promise<{ selected_id: string }> {
  return request('/api/software/selection', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ software_id: softwareId }),
  })
}

export function createTask(conversation: ConversationRequest): Promise<Conversation> {
  return request<Conversation>('/api/tasks', { method: 'POST', body: conversationForm(conversation) })
}

export function getTasks(): Promise<TaskHistoryResponse> {
  return request<TaskHistoryResponse>('/api/tasks')
}

export function getSolutions(): Promise<TaskHistoryResponse> {
  return request<TaskHistoryResponse>('/api/solutions')
}

export function createSolution(question: string, image: File | null, conversationId: string | null, requestId?: string): Promise<Conversation> {
  const body = new FormData()
  body.append('question', question)
  if (image) body.append('image', image)
  if (conversationId) body.append('conversation_id', conversationId)
  if (requestId) body.append('request_id', requestId)
  return request<Conversation>('/api/solutions', { method: 'POST', body })
}

export function deleteSolution(conversationId: string): Promise<void> {
  return request<void>(`/api/solutions/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })
}

export async function getSolutionImage(conversationId: string, imagePath: string): Promise<Blob> {
  const parts = imagePath.split('/')
  if (parts.length !== 3 || parts[0] !== 'solutions') throw new Error(requestText.invalidImagePath)
  const token = readToken()
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${apiBase}/api/solutions/${encodeURIComponent(conversationId)}/images/${encodeURIComponent(parts[1])}/${encodeURIComponent(parts[2])}`, { headers })
  if (!response.ok) throw new Error(requestText.solutionImageFailed)
  return response.blob()
}

export async function getSolutionInputImage(conversationId: string, messageId: string): Promise<Blob> {
  const token = readToken()
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${apiBase}/api/solutions/${encodeURIComponent(conversationId)}/input-images/${encodeURIComponent(messageId)}`, { headers })
  if (!response.ok) throw new Error(requestText.questionImageFailed)
  return response.blob()
}

export function getTaskProgress(requestId: string): Promise<{ stage: string; done: boolean; conversation_id?: string }> {
  return request(`/api/task-progress/${encodeURIComponent(requestId)}`)
}

export function getRoommateChats(): Promise<TaskHistoryResponse> {
  return request('/api/roommate')
}

export function deleteRoommateChat(conversationId: string): Promise<void> {
  return request(`/api/roommate/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })
}

export function sendRoommateMessage(question: string, images: File[], conversationId: string | null, requestId: string, files: File[] = []): Promise<Conversation> {
  const body = new FormData()
  body.append('question', question)
  images.forEach((image) => body.append('images', image))
  files.forEach((file) => body.append('files', file))
  if (conversationId) body.append('conversation_id', conversationId)
  body.append('request_id', requestId)
  return request('/api/roommate', { method: 'POST', body })
}

export async function getRoommateImage(conversationId: string, path: string): Promise<Blob> {
  if (!/^(?:images|input-images)\/[^/]+\/[^/]+$/.test(path)) throw new Error(requestText.invalidImagePath)
  const encoded = path.split('/').map(encodeURIComponent).join('/')
  return (await requestResponse(`/api/roommate/${encodeURIComponent(conversationId)}/${encoded}`)).blob()
}

export function getProjects(): Promise<{ items: ProjectSummary[] }> { return request('/api/projects') }
export function getProject(id: string): Promise<ProjectDetail> { return request(`/api/projects/${encodeURIComponent(id)}`) }

type McpToolContent = {
  type: string; text?: string; data?: string; mimeType?: string
  resource?: { blob?: string; mimeType?: string }
}

async function requestMcpTool<T>(name: string, args: Record<string, unknown>, signal?: AbortSignal) {
  const reply = await request<{
    error?: { message: string }
    result?: { isError?: boolean; content?: McpToolContent[]; structuredContent?: T }
  }>('/mcp/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } }),
    signal,
  })
  if (reply.error) throw new Error(reply.error.message)
  if (reply.result?.isError) throw new Error(reply.result.content?.[0]?.text || 'MCP 工具调用失败。')
  if (!reply.result) throw new Error('MCP 服务未返回结果。')
  return reply.result
}

async function callMcpTool<T>(name: string, args: Record<string, unknown>): Promise<T> {
  const { structuredContent } = await requestMcpTool<T>(name, args)
  if (structuredContent === undefined) throw new Error('MCP 服务未返回结果。')
  return structuredContent
}

export function searchMcpPapers(query: string): Promise<{ papers: McpPaper[] }> {
  return callMcpTool('search_papers', { query })
}

export function readMcpPdf(conversationId: string, filePath: string, startPage: number, endPage: number): Promise<McpPdfResult> {
  return callMcpTool('read_pdf_pages', {
    conversation_id: conversationId, file_id: filePath, start_page: startPage, end_page: endPage,
  })
}

export type McpExperimentTask = {
  conversation_id: string
  request_id: string
  status: 'queued' | 'running' | 'answered' | 'error' | 'draft' | 'interrupted'
  stage?: string
  reply?: string | null
  outputs?: ProjectFile[]
}

export function submitMcpExperiment(conversationId: string, softwareId: string, requirements: string,
                                    deliverables: string[], requestId: string): Promise<McpExperimentTask> {
  return callMcpTool('submit_experiment_task', {
    conversation_id: conversationId, software_id: softwareId, requirements, deliverables,
    request_id: requestId, confirm_execution: true,
  })
}

export async function getMcpExperiment(conversationId: string, requestId: string, signal?: AbortSignal): Promise<McpExperimentTask> {
  const { structuredContent } = await requestMcpTool<McpExperimentTask>('get_experiment_task', {
    conversation_id: conversationId, request_id: requestId,
  }, signal)
  if (!structuredContent) throw new Error('MCP 服务未返回任务状态。')
  return structuredContent
}

export async function previewMcpPdfPage(conversationId: string, filePath: string, page: number, signal: AbortSignal): Promise<string> {
  const { content } = await requestMcpTool('preview_pdf_page', {
    conversation_id: conversationId, file_id: filePath, page_number: page,
  }, signal)
  const image = content?.find((item) => item.type === 'image' && item.mimeType === 'image/jpeg')
  if (!image?.data) throw new Error('MCP 服务未返回 PDF 预览。')
  return `data:image/jpeg;base64,${image.data}`
}

const mcpArtifactFiles = {
  render_presentation: ['presentation.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'],
  render_thesis: ['manuscript.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  plot_discrete_sequence: ['sequence.png', 'image/png'],
} as const

export type McpArtifactTool = keyof typeof mcpArtifactFiles

export async function runMcpArtifact(tool: McpArtifactTool, args: Record<string, unknown>) {
  const [filename, mimeType] = mcpArtifactFiles[tool]
  const { content } = await requestMcpTool(tool, args)
  const blob = mimeType === 'image/png'
    ? content?.find((item) => item.type === 'image' && item.mimeType === mimeType)?.data
    : content?.find((item) => item.type === 'resource' && item.resource?.mimeType === mimeType)?.resource?.blob
  if (!blob) throw new Error('MCP 服务未返回生成文件。')
  return { filename, href: `data:${mimeType};base64,${blob}`, image: mimeType === 'image/png' }
}

export type ModelRole = 'text' | 'vision'
export type OutputDetail = 'detailed' | 'normal' | 'brief'

export function getOutputSettings(): Promise<{ detail: OutputDetail }> {
  return request('/api/settings/output')
}

export function updateOutputSettings(detail: OutputDetail): Promise<{ detail: OutputDetail }> {
  return request('/api/settings/output', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ detail }),
  })
}
export type ModelSettings = { current: string; options: string[]; error: string | null; revision: string | null }
export type ModelConnection = { base_url: string; api_key: string }

export function getModelSettings(role: ModelRole): Promise<ModelSettings> {
  return request(`/api/settings/models/${role}`)
}

export async function previewModelConnection(role: ModelRole, connection: ModelConnection): Promise<string[]> {
  const { options } = await request<{ options: string[] }>(`/api/settings/models/${role}/connection`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(connection),
  })
  return options
}

export function updateModelSettings(role: ModelRole, revision: string | null, model: string, connection?: ModelConnection): Promise<{ revision: string }> {
  return request(`/api/settings/models/${role}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
    revision, model, ...(connection ? { connection } : {}),
  }) })
}

export async function downloadProjectFile(id: string, file: ProjectFile): Promise<void> {
  const path = file.path.split('/').map(encodeURIComponent).join('/')
  const headers = new Headers()
  const token = readToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(id)}/files/${path}`, { headers })
  if (!response.ok) {
    if (response.status === 401) {
      clearToken()
      window.dispatchEvent(new Event('tongxue-auth-expired'))
    }
    throw new Error(requestText.projectDownloadFailed(response.status))
  }
  saveDownload(await response.blob(), file.name)
}

function saveDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function deleteTask(conversationId: string): Promise<void> {
  return request<void>(`/api/tasks/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })
}

export async function downloadReport(conversationId: string, messageId: string, filename: string): Promise<void> {
  const token = readToken()
  const response = await fetch(`${apiBase}/api/tasks/${encodeURIComponent(conversationId)}/reports/${encodeURIComponent(messageId)}/${encodeURIComponent(filename)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) {
    if (response.status === 401) {
      clearToken()
      window.dispatchEvent(new Event('tongxue-auth-expired'))
    }
    throw new Error(requestText.reportDownloadFailed(response.status))
  }
  saveDownload(await response.blob(), filename)
}
