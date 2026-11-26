export type Deliverable = 'report' | 'paper' | 'code'

export interface Software {
  id: string
  name: string
}

export interface SoftwareResponse {
  items: Software[]
  selected_id: string | null
}

export type { Conversation, ConversationMessage, TaskHistoryResponse } from './contracts/responses/conversation'
