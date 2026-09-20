/**
 * How the assistant should answer.
 *
 * `fast` is a short direct reply. `deep` retrieves more of the archive, lets the
 * model reason first and asks for a structured report, so it is much slower.
 * The values are what the chat endpoint expects in its `mode` field.
 */
export type AiMode = 'fast' | 'deep'

export const AI_MODES: ReadonlyArray<{
  id: AiMode
  label: string
  hint: string
}> = [
  {
    id: 'fast',
    label: 'إجابة سريعة',
    hint: 'رد مختصر ومباشر في أسرع وقت',
  },
  {
    id: 'deep',
    label: 'تفكير عميق',
    hint: 'تحليل أوسع للمستندات وتقرير مفصّل، لكنه أبطأ',
  },
]

export function aiModeLabel(mode: AiMode | undefined): string {
  return AI_MODES.find((m) => m.id === mode)?.label ?? ''
}

export interface AiMessage {
  role: 'user' | 'assistant'
  content: string
  /** Mode the answer was produced in. Only set on assistant messages. */
  mode?: AiMode
  createdAt: number
  /** How long the answer took, in milliseconds. */
  elapsedMs?: number
  /** True while the answer is still arriving. Never persisted. */
  isStreaming?: boolean
  /** True when the request failed or was stopped before completing. */
  incomplete?: boolean
}

export interface AiConversation {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: AiMessage[]
}
