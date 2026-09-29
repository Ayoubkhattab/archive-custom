import { Injectable, inject, signal } from '@angular/core'
import { Subject, Subscription } from 'rxjs'
import { AiMode } from '../data/ai-conversation'
import { Document } from '../data/document'
import {
  CheckedQuote,
  ParsedAnswer,
  checkQuotes,
  parseAnswer,
} from '../utils/answer-sources'
import {
  describeHttpFailure,
  isProxyErrorBody,
  stripHeartbeats,
} from '../utils/stream-errors'
import { ChatMessage, ChatService } from './chat.service'
import { DocumentService } from './rest/document.service'

export type AskMessageState = 'waiting' | 'streaming' | 'done' | 'error' | 'stopped'

export interface AskMessage {
  role: 'user' | 'assistant'
  content: string
  state?: AskMessageState
  mode?: AiMode
  /** Milliseconds from sending to the first word, and to the last. */
  firstWordMs?: number
  totalMs?: number
  context?: 'complete' | 'excerpts'
  parsed?: ParsedAnswer
  quotes?: CheckedQuote[]
}

export interface AskConversation {
  documentId: number
  messages: AskMessage[]
}

/**
 * "Ask about this file": a side panel for questions about one document,
 * separate from the general AI chat.
 *
 * Holds one conversation per document for the browser session, so closing the
 * panel, moving to another document and coming back keeps the thread. The
 * panel itself (DocumentAskPanelComponent) lives in the app frame and just
 * renders this state.
 */
@Injectable({
  providedIn: 'root',
})
export class DocumentAskService {
  private chatService = inject(ChatService)
  private documentService = inject(DocumentService)

  readonly isOpen = signal(false)
  readonly document = signal<Document>(null)
  readonly loadingDocument = signal(false)
  readonly mode = signal<AiMode>('fast')

  /** Asks the document page to select a quote in the content editor. */
  readonly locateQuote$ = new Subject<{ documentId: number; quote: string }>()

  /** Emits whenever a conversation changes, so the panel can re-render. */
  readonly changed$ = new Subject<void>()

  private conversations = new Map<number, AskConversation>()
  private running: Subscription = null

  get conversation(): AskConversation {
    const doc = this.document()
    return doc ? this.conversationFor(doc.id) : null
  }

  get busy(): boolean {
    return this.running !== null
  }

  /** Open the panel for a document, fetching it if only an id is known. */
  open(document: Document | number) {
    const id = typeof document === 'number' ? document : document?.id
    if (!id) return
    this.isOpen.set(true)

    if (typeof document !== 'number' && document.content != null) {
      this.document.set(document)
      return
    }
    if (this.document()?.id === id && this.document().content != null) return

    this.loadingDocument.set(true)
    this.documentService.get(id).subscribe({
      next: (doc) => {
        this.document.set(doc)
        this.loadingDocument.set(false)
      },
      error: () => {
        this.loadingDocument.set(false)
        this.isOpen.set(false)
      },
    })
  }

  close() {
    this.isOpen.set(false)
  }

  toggle(document: Document | number) {
    const id = typeof document === 'number' ? document : document?.id
    if (this.isOpen() && this.document()?.id === id) {
      this.close()
    } else {
      this.open(document)
    }
  }

  clearConversation() {
    this.stop()
    const doc = this.document()
    if (doc) this.conversations.delete(doc.id)
    this.changed$.next()
  }

  ask(question: string) {
    const doc = this.document()
    const text = question?.trim()
    if (!doc || !text || this.busy) return

    const conversation = this.conversationFor(doc.id)
    // Earlier finished turns only: a failed or stopped answer would teach the
    // model nothing and could mislead the follow-up.
    const history: ChatMessage[] = []
    for (let i = 0; i + 1 < conversation.messages.length; i += 2) {
      const q = conversation.messages[i]
      const a = conversation.messages[i + 1]
      if (q.role === 'user' && a.role === 'assistant' && a.state === 'done') {
        history.push({ role: 'user', content: q.content })
        history.push({ role: 'assistant', content: a.content })
      }
    }

    const mode = this.mode()
    conversation.messages.push({ role: 'user', content: text })
    const answer: AskMessage = {
      role: 'assistant',
      content: '',
      state: 'waiting',
      mode,
    }
    conversation.messages.push(answer)
    this.changed$.next()

    const started = performance.now()
    // Set when the stream ends; an already-finished stream (a synchronous
    // error, say) must not be left behind as the running one.
    let ended = false
    const subscription = this.chatService
      .askDocument(doc.id, text, history, mode)
      .subscribe({
        next: (chunk) => {
          if (chunk.context) answer.context = chunk.context
          if (chunk.text !== undefined) {
            const visible = stripHeartbeats(chunk.text)
            if (visible.trim() && answer.firstWordMs === undefined) {
              answer.firstWordMs = performance.now() - started
            }
            answer.content = visible
            if (visible.trim()) answer.state = 'streaming'
          }
          this.changed$.next()
        },
        error: (error) => {
          const serverText =
            typeof error?.error === 'string' && error.status === 400
              ? error.error
              : null
          answer.content = serverText
            ? this.describeBadRequest(serverText)
            : describeHttpFailure(error?.status)
          answer.state = 'error'
          answer.totalMs = performance.now() - started
          ended = true
          this.running = null
          this.changed$.next()
        },
        complete: () => {
          answer.totalMs = performance.now() - started
          ended = true
          this.running = null
          if (isProxyErrorBody(answer.content) || !answer.content.trim()) {
            answer.content = describeHttpFailure(undefined)
            answer.state = 'error'
          } else {
            this.finish(answer, doc)
          }
          this.changed$.next()
        },
      })
    if (!ended) this.running = subscription
  }

  /** Stop the answer being written; what arrived so far is kept. */
  stop() {
    if (!this.running) return
    this.running.unsubscribe()
    this.running = null
    const messages = this.conversation?.messages ?? []
    const last = messages[messages.length - 1]
    if (last?.role === 'assistant' && last.state !== 'done') {
      last.state = last.content.trim() ? 'stopped' : 'error'
      if (!last.content.trim()) {
        last.content = $localize`:@@ask.stopped:Stopped.`
      }
      const doc = this.document()
      if (last.content.trim() && doc) this.finish(last, doc, 'stopped')
    }
    this.changed$.next()
  }

  locate(quote: string) {
    const doc = this.document()
    if (doc) this.locateQuote$.next({ documentId: doc.id, quote })
  }

  private finish(
    answer: AskMessage,
    doc: Document,
    state: AskMessageState = 'done'
  ) {
    answer.state = state
    answer.parsed = parseAnswer(answer.content)
    answer.quotes = checkQuotes(answer.parsed.quotes, doc.content ?? '')
  }

  private conversationFor(documentId: number): AskConversation {
    let conversation = this.conversations.get(documentId)
    if (!conversation) {
      conversation = { documentId, messages: [] }
      this.conversations.set(documentId, conversation)
    }
    return conversation
  }

  private describeBadRequest(serverText: string): string {
    if (/no extracted text/i.test(serverText)) {
      return $localize`:@@ask.error.noText:This document has no extracted text yet, so it cannot be asked about. Wait for processing (OCR) to finish.`
    }
    if (/AI is required/i.test(serverText)) {
      return $localize`:@@ask.error.aiDisabled:AI features are turned off in the configuration.`
    }
    return describeHttpFailure(400)
  }
}
