import { inject, Injectable } from '@angular/core'
import {
  AiConversation,
  AiMessage,
  AiMode,
  AI_MODES,
} from 'src/app/data/ai-conversation'
import { SettingsService } from './settings.service'

/** Oldest conversations are dropped beyond this many. */
export const MAX_CONVERSATIONS = 50

const STORAGE_PREFIX = 'pngx-ai-conversations'
const MODE_PREFIX = 'pngx-ai-mode'

/**
 * Keeps the AI conversations in this browser's localStorage.
 *
 * Nothing here is sent to the server, so history lives only on this device and
 * disappears if the browser data is cleared. Storage can be unavailable (private
 * windows, blocked site data) or full, so every access is guarded and a failure
 * degrades to "not saved" instead of breaking the page.
 *
 * Keys include the username: several people can share one browser, and one
 * person's questions must not show up in another's history.
 */
@Injectable({
  providedIn: 'root',
})
export class AiConversationStoreService {
  private settings = inject(SettingsService)

  private get suffix(): string {
    return this.settings.currentUser?.username ?? 'anonymous'
  }

  private get key(): string {
    return `${STORAGE_PREFIX}:${this.suffix}`
  }

  /** All saved conversations, most recently updated first. */
  list(): AiConversation[] {
    try {
      const parsed = JSON.parse(localStorage.getItem(this.key) ?? '[]')
      if (!Array.isArray(parsed)) return []
      return parsed
        .filter(isConversation)
        .sort((a, b) => b.updatedAt - a.updatedAt)
    } catch {
      return []
    }
  }

  /**
   * Inserts or replaces a conversation.
   * @returns false when the browser refused to store it.
   */
  save(conversation: AiConversation): boolean {
    const stored: AiConversation = {
      ...conversation,
      // An answer that has not arrived yet must not be saved half-written.
      messages: conversation.messages
        .filter((m) => !(m.isStreaming && !m.content))
        .map(toStoredMessage),
    }

    let all = [stored, ...this.list().filter((c) => c.id !== stored.id)]
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_CONVERSATIONS)

    // A full quota is the usual failure. Drop the oldest until it fits,
    // but never the conversation being saved.
    while (all.length > 0) {
      try {
        localStorage.setItem(this.key, JSON.stringify(all))
        return true
      } catch {
        if (all.length === 1) return false
        all = all.slice(0, -1)
      }
    }
    return false
  }

  delete(id: string): void {
    this.write(this.list().filter((c) => c.id !== id))
  }

  clear(): void {
    try {
      localStorage.removeItem(this.key)
    } catch {
      // Nothing to clear if storage is unavailable.
    }
  }

  getMode(): AiMode {
    try {
      const mode = localStorage.getItem(`${MODE_PREFIX}:${this.suffix}`)
      return AI_MODES.some((m) => m.id === mode) ? (mode as AiMode) : 'fast'
    } catch {
      return 'fast'
    }
  }

  setMode(mode: AiMode): void {
    try {
      localStorage.setItem(`${MODE_PREFIX}:${this.suffix}`, mode)
    } catch {
      // The choice simply is not remembered.
    }
  }

  private write(conversations: AiConversation[]): void {
    try {
      localStorage.setItem(this.key, JSON.stringify(conversations))
    } catch {
      // Deleting frees space, so this practically cannot fail; ignore if it does.
    }
  }
}

export function newConversationId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  )
}

function toStoredMessage(message: AiMessage): AiMessage {
  const { isStreaming, ...rest } = message
  return isStreaming ? { ...rest, incomplete: true } : rest
}

/** Guards against corrupt or hand-edited storage before it reaches the UI. */
function isConversation(value: any): value is AiConversation {
  return (
    !!value &&
    typeof value.id === 'string' &&
    typeof value.title === 'string' &&
    typeof value.updatedAt === 'number' &&
    Array.isArray(value.messages) &&
    value.messages.every(
      (m) =>
        m &&
        (m.role === 'user' || m.role === 'assistant') &&
        typeof m.content === 'string'
    )
  )
}
