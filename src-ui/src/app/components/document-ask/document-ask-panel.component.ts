import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DOCUMENT,
  DestroyRef,
  ElementRef,
  NgZone,
  SecurityContext,
  ViewChild,
  effect,
  inject,
} from '@angular/core'
import { takeUntilDestroyed } from '@angular/core/rxjs-interop'
import { FormsModule } from '@angular/forms'
import { DomSanitizer, SafeHtml } from '@angular/platform-browser'
import { Router } from '@angular/router'
import { marked } from 'marked'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { AiMode } from 'src/app/data/ai-conversation'
import {
  AskMessage,
  DocumentAskService,
} from 'src/app/services/document-ask.service'
import { ToastService } from 'src/app/services/toast.service'
import { QuoteStatus } from 'src/app/utils/answer-sources'

@Component({
  selector: 'pngx-document-ask-panel',
  templateUrl: './document-ask-panel.component.html',
  styleUrl: './document-ask-panel.component.scss',
  imports: [FormsModule, NgxBootstrapIconsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DocumentAskPanelComponent {
  ask = inject(DocumentAskService)
  private router = inject(Router)
  private sanitizer = inject(DomSanitizer)
  private toastService = inject(ToastService)
  private cdr = inject(ChangeDetectorRef)
  private zone = inject(NgZone)
  private document = inject(DOCUMENT)
  private destroyRef = inject(DestroyRef)

  @ViewChild('input') input: ElementRef<HTMLTextAreaElement>
  @ViewChild('scrollBody') scrollBody: ElementRef<HTMLElement>

  public draft = ''
  public QuoteStatus = QuoteStatus

  public readonly suggestions: string[] = [
    $localize`:@@ask.suggest.summary:Summarize this document in a few points`,
    $localize`:@@ask.suggest.subject:What is the subject, date and reference number?`,
    $localize`:@@ask.suggest.parties:Who are the parties or people mentioned?`,
    $localize`:@@ask.suggest.dates:What are the important dates and deadlines?`,
    $localize`:@@ask.suggest.amounts:What amounts and figures are mentioned?`,
    $localize`:@@ask.suggest.actions:What is required, and by whom?`,
  ]

  private renderPending = false
  private markdownCache = new WeakMap<
    AskMessage,
    { content: string; html: SafeHtml }
  >()

  constructor() {
    this.ask.changed$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.scheduleRender())

    // Wide screens make room for the panel instead of covering the page.
    effect(() => {
      const open = this.ask.isOpen()
      this.document.body.classList.toggle('pngx-ask-open', open)
      if (open) {
        setTimeout(() => {
          this.input?.nativeElement.focus()
          this.scrollToBottom()
        })
      }
    })
  }

  get messages(): AskMessage[] {
    return this.ask.conversation?.messages ?? []
  }

  get onDocumentPage(): boolean {
    const id = this.ask.document()?.id
    return !!id && this.router.url.split(/[?#]/)[0].startsWith(`/documents/${id}`)
  }

  setMode(mode: AiMode) {
    this.ask.mode.set(mode)
  }

  send(question: string = this.draft) {
    if (!question.trim() || this.ask.busy) return
    this.ask.ask(question)
    this.draft = ''
    setTimeout(() => this.input?.nativeElement.focus())
  }

  onKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      this.send()
    }
  }

  onPanelKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      this.ask.close()
    }
  }

  close() {
    this.ask.close()
  }

  newConversation() {
    this.ask.clearConversation()
    setTimeout(() => this.input?.nativeElement.focus())
  }

  locate(quote: string) {
    this.ask.locate(quote)
  }

  copy(text: string) {
    navigator.clipboard
      ?.writeText(text)
      .then(() =>
        this.toastService.showInfo($localize`:@@ask.copied:Copied to clipboard`)
      )
      .catch(() =>
        this.toastService.showError($localize`Unable to copy response`)
      )
  }

  seconds(ms: number): string {
    return (ms / 1000).toFixed(1)
  }

  quoteLabel(status: QuoteStatus): string {
    switch (status) {
      case QuoteStatus.Exact:
        return $localize`:@@ask.quote.exact:Found in the document`
      case QuoteStatus.Partial:
        return $localize`:@@ask.quote.partial:Close match in the document`
      default:
        return $localize`:@@ask.quote.missing:Not found in the document — check manually`
    }
  }

  quoteIcon(status: QuoteStatus): string {
    switch (status) {
      case QuoteStatus.Exact:
        return 'check-circle-fill'
      case QuoteStatus.Partial:
        return 'check-circle'
      default:
        return 'exclamation-triangle-fill'
    }
  }

  // Parsing markdown is cached per message: the template calls this on every
  // change detection pass.
  renderMarkdown(message: AskMessage): SafeHtml {
    const text = message.parsed?.body ?? message.content
    const cached = this.markdownCache.get(message)
    if (cached && cached.content === text) return cached.html
    const html = marked.parse(text ?? '', { async: false, breaks: true }) as string
    const sanitized = this.sanitizer.sanitize(SecurityContext.HTML, html) ?? ''
    const safe = this.sanitizer.bypassSecurityTrustHtml(sanitized)
    this.markdownCache.set(message, { content: text, html: safe })
    return safe
  }

  // One render per animation frame at most while an answer streams in, and
  // only this component rather than the whole application.
  private scheduleRender() {
    if (this.renderPending) return
    this.renderPending = true
    this.zone.runOutsideAngular(() =>
      requestAnimationFrame(() => {
        this.renderPending = false
        this.cdr.detectChanges()
        this.scrollToBottom()
      })
    )
  }

  private scrollToBottom() {
    const body = this.scrollBody?.nativeElement
    if (body) body.scrollTop = body.scrollHeight
  }
}
