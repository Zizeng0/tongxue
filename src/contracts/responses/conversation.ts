export interface ConversationMessage {
  id: string
  created_at: string
  requirements: string
  software_id?: string | null
  guide_name: string | null
  report_template_name?: string | null
  report_files?: string[]
  artifact_files?: string[]
  attachment_names: string[]
  output_images?: string[]
  other_file_names?: string[]
  reply?: string | null
  exercise_candidates?: string | null
  vision_status?: string | null
  status?: 'draft' | 'processing' | 'answered' | 'error'
  request_id?: string | null
  pending?: boolean
  preview_file?: File
  processing_seconds?: number | null
  stage_seconds?: Record<string, number>
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  messages: ConversationMessage[]
}

export interface TaskHistoryResponse {
  items: Conversation[]
}
