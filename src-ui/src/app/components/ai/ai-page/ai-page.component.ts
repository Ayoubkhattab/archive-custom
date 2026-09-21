import {
  ChangeDetectorRef,
  Component,
  ElementRef,
  inject,
  NgZone,
  OnDestroy,
  OnInit,
  SecurityContext,
  ViewChild,
} from '@angular/core'
import { FormsModule } from '@angular/forms'
import { DomSanitizer, SafeHtml } from '@angular/platform-browser'
import { NgbDropdownModule } from '@ng-bootstrap/ng-bootstrap'
import { marked } from 'marked'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { Subscription } from 'rxjs'
import {
  AI_MODES,
  AiConversation,
  AiMessage,
  AiMode,
  aiModeLabel,
} from 'src/app/data/ai-conversation'
import { SETTINGS_KEYS } from 'src/app/data/ui-settings'
import {
  AiConversationStoreService,
  newConversationId,
} from 'src/app/services/ai-conversation-store.service'
import {
  AiReport,
  AiReportExportService,
  conversationToReport,
} from 'src/app/services/ai-report-export.service'
import { ChatService } from 'src/app/services/chat.service'
import { SettingsService } from 'src/app/services/settings.service'
import { ToastService } from 'src/app/services/toast.service'
import { PageHeaderComponent } from '../../common/page-header/page-header.component'

@Component({
  selector: 'pngx-ai-page',
  templateUrl: './ai-page.component.html',
  styleUrls: ['./ai-page.component.scss'],
  imports: [
    FormsModule,
    NgbDropdownModule,
    NgxBootstrapIconsModule,
    PageHeaderComponent,
  ],
})
export class AiPageComponent implements OnInit, OnDestroy {
  private chatService = inject(ChatService)
  private store = inject(AiConversationStoreService)
  private exporter = inject(AiReportExportService)
  private settings = inject(SettingsService)
  private toastService = inject(ToastService)
  private sanitizer = inject(DomSanitizer)
  private zone = inject(NgZone)
  private cdr = inject(ChangeDetectorRef)

  readonly modes = AI_MODES

  conversations: AiConversation[] = []
  current: AiConversation = this.blankConversation()
  mode: AiMode = 'fast'
  input = ''
  loading = false
  /** Seconds since the question was sent, shown while waiting. */
  elapsed = 0
  showHistory = false
  exporting = false
  /** False when this browser refused to keep the history. */
  storageOk = true

  @ViewChild('scrollAnchor') private scrollAnchor?: ElementRef<HTMLElement>
  @ViewChild('composer') private composer?: ElementRef<HTMLTextAreaElement>

  private subscription?: Subscription
  private timer?: ReturnType<typeof setInterval>
  private startedAt = 0
  private scrollPending = false
  private markdownCache = new WeakMap<
    AiMessage,
    { content: string; html: SafeHtml }
  >()

  get aiEnabled(): boolean {
    return !!this.settings.get(SETTINGS_KEYS.AI_ENABLED)
  }

  get canExport(): boolean {
    return this.exportableEntries > 0
  }

  private get exportableEntries(): number {
    return conversationToReport(this.current).entries.length
  }

  get streaming(): AiMessage | undefined {
    return this.current.messages.find((m) => m.isStreaming)
  }

  ngOnInit(): void {
    this.mode = this.store.getMode()
    this.refreshHistory()
  }

  ngOnDestroy(): void {
    // Leaving the page cancels the request; whatever arrived is kept.
    this.stopTimer()
    if (this.loading) this.stop()
  }

  aiModeLabel = aiModeLabel

  setMode(mode: AiMode): void {
    this.mode = mode
    this.store.setMode(mode)
  }

  newConversation(): void {
    if (this.loading) this.stop()
    this.current = this.blankConversation()
    this.showHistory = false
    this.focusComposer()
  }

  openConversation(conversation: AiConversation): void {
    if (this.loading) this.stop()
    // A copy, so editing the open one does not alter the list until saved.
    this.current = JSON.parse(JSON.stringify(conversation))
    this.showHistory = false
    this.scrollToBottom()
  }

  deleteConversation(conversation: AiConversation, event: Event): void {
    event.stopPropagation()
    this.store.delete(conversation.id)
    if (this.current.id === conversation.id) {
      if (this.loading) this.stop()
      this.current = this.blankConversation()
    }
    this.refreshHistory()
  }

  clearHistory(): void {
    if (!confirm('هل تريد حذف كل المحادثات المحفوظة في هذا المتصفح؟')) return
    if (this.loading) this.stop()
    this.store.clear()
    this.current = this.blankConversation()
    this.refreshHistory()
  }

  send(): void {
    const question = this.input.trim()
    if (!question || this.loading) return
    if (question.length > 4000) {
      this.toastService.showError('السؤال طويل جداً، الحد الأقصى ٤٠٠٠ حرف.')
      return
    }

    const now = Date.now()
    const answer: AiMessage = {
      role: 'assistant',
      content: '',
      mode: this.mode,
      createdAt: now,
      isStreaming: true,
    }
    this.current.messages.push(
      { role: 'user', content: question, createdAt: now },
      answer
    )
    if (!this.current.title) {
      this.current.title = question.replace(/\s+/g, ' ').slice(0, 60)
    }
    this.input = ''
    this.loading = true
    this.persist()
    this.startTimer()
    this.scrollToBottom()

    this.subscription = this.chatService
      .streamChat(undefined, question, this.mode)
      .subscribe({
        // Each event carries the whole text received so far.
        next: (text) => {
          answer.content = text
          this.scrollToBottom()
        },
        error: () => {
          answer.isStreaming = false
          answer.incomplete = true
          answer.content = answer.content
            ? `${answer.content}\n\n⚠️ انقطع الاتصال قبل اكتمال الإجابة.`
            : '⚠️ تعذّر الحصول على رد من نموذج الذكاء الاصطناعي، يرجى المحاولة مجدداً.'
          this.finish(answer)
        },
        complete: () => {
          answer.isStreaming = false
          this.finish(answer)
        },
      })
  }

  /** Cancels the request in flight, keeping whatever has already arrived. */
  stop(): void {
    const answer = this.streaming
    this.subscription?.unsubscribe()
    if (answer) {
      answer.isStreaming = false
      answer.incomplete = true
      if (!answer.content) answer.content = '⏹ أُوقفت الإجابة.'
      this.finish(answer)
    }
  }

  onComposerKeydown(event: KeyboardEvent): void {
    // Enter sends; Shift+Enter adds a line. Ignored while an IME is composing,
    // where Enter confirms the composition instead.
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      this.send()
    }
  }

  renderMarkdown(message: AiMessage): SafeHtml {
    const cached = this.markdownCache.get(message)
    if (cached && cached.content === message.content) return cached.html
    const html = marked.parse(message.content ?? '', {
      async: false,
      breaks: true,
    }) as string
    const sanitized = this.sanitizer.sanitize(SecurityContext.HTML, html) ?? ''
    const safe = this.sanitizer.bypassSecurityTrustHtml(sanitized)
    this.markdownCache.set(message, { content: message.content, html: safe })
    return safe
  }

  copyMessage(message: AiMessage): void {
    navigator.clipboard
      .writeText(message.content)
      .then(() => this.toastService.showInfo('تم نسخ الإجابة'))
      .catch(() => this.toastService.showError('تعذّر نسخ الإجابة'))
  }

  exportAnswer(message: AiMessage, format: 'pdf' | 'md'): void {
    const index = this.current.messages.indexOf(message)
    const question = this.current.messages
      .slice(0, index)
      .reverse()
      .find((m) => m.role === 'user')
    this.runExport(
      {
        title: question?.content.slice(0, 60) || this.current.title,
        generatedAt: Date.now(),
        entries: [
          {
            question: question?.content,
            answer: message.content,
            mode: message.mode,
          },
        ],
      },
      format
    )
  }

  exportConversation(format: 'pdf' | 'md'): void {
    this.runExport(conversationToReport(this.current), format)
  }

  formatDate(timestamp: number): string {
    return new Date(timestamp).toLocaleString('ar', {
      dateStyle: 'medium',
      timeStyle: 'short',
    })
  }

  formatElapsed(ms: number | undefined): string {
    if (!ms) return ''
    const seconds = Math.round(ms / 1000)
    return seconds < 60
      ? `${seconds} ث`
      : `${Math.floor(seconds / 60)} د ${seconds % 60} ث`
  }

  private async runExport(report: AiReport, format: 'pdf' | 'md') {
    if (!report.entries.length || this.exporting) return
    this.exporting = true
    try {
      if (format === 'pdf') await this.exporter.downloadPdf(report)
      else this.exporter.downloadMarkdown(report)
    } catch (error) {
      this.toastService.showError('تعذّر تصدير التقرير', error)
    } finally {
      this.exporting = false
      this.cdr.markForCheck()
    }
  }

  private finish(answer: AiMessage): void {
    this.stopTimer()
    this.loading = false
    answer.elapsedMs = Date.now() - this.startedAt
    this.current.updatedAt = Date.now()
    this.persist()
    this.cdr.markForCheck()
    this.scrollToBottom()
    this.focusComposer()
  }

  private persist(): void {
    if (!this.current.messages.length) return
    this.current.updatedAt = Date.now()
    this.storageOk = this.store.save(this.current)
    this.refreshHistory()
  }

  private refreshHistory(): void {
    this.conversations = this.store.list()
  }

  private startTimer(): void {
    this.startedAt = Date.now()
    this.elapsed = 0
    this.stopTimer()
    this.timer = setInterval(() => {
      this.elapsed = Math.floor((Date.now() - this.startedAt) / 1000)
      this.cdr.markForCheck()
    }, 1000)
  }

  private stopTimer(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = undefined
  }

  private blankConversation(): AiConversation {
    const now = Date.now()
    return {
      id: newConversationId(),
      title: '',
      createdAt: now,
      updatedAt: now,
      messages: [],
    }
  }

  // At most one scroll per frame, and not an animated one: a smooth scroll
  // restarted for every chunk never catches up with the text.
  private scrollToBottom(): void {
    if (this.scrollPending) return
    this.scrollPending = true
    this.zone.runOutsideAngular(() =>
      requestAnimationFrame(() => {
        this.scrollPending = false
        this.scrollAnchor?.nativeElement.scrollIntoView({ block: 'end' })
      })
    )
  }

  private focusComposer(): void {
    setTimeout(() => this.composer?.nativeElement.focus(), 0)
  }
}
