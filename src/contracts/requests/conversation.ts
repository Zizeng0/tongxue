import type { Deliverable } from '../../types'

// 字段名与 FastAPI 的 multipart/form-data 参数保持一致。
export interface ConversationRequest {
  requirements: string
  software_id: string
  deliverables: Deliverable[]
  conversation_id: string | null
  guide: File | null
  report_template: File | null
  attachments: File[]
  other_files: File[]
  request_id?: string
}

export function conversationForm(request: ConversationRequest): FormData {
  const form = new FormData()
  form.append('requirements', request.requirements)
  form.append('software_id', request.software_id)
  if (request.request_id) form.append('request_id', request.request_id)
  request.deliverables.forEach((item) => form.append('deliverables', item))
  if (request.conversation_id) form.append('conversation_id', request.conversation_id)
  if (request.guide) form.append('guide', request.guide)
  if (request.report_template) form.append('report_template', request.report_template)
  request.attachments.forEach((file) => form.append('attachments', file))
  request.other_files.forEach((file) => form.append('other_files', file))
  return form
}
