import {
  ChangeDetectorRef,
  Component,
  ElementRef,
  inject,
  NgZone,
  OnInit,
  SecurityContext,
  ViewChild,
} from '@angular/core'
import { FormsModule, ReactiveFormsModule } from '@angular/forms'
import { DomSanitizer, SafeHtml } from '@angular/platform-browser'
import { NavigationEnd, Router } from '@angular/router'
import { NgbDropdown, NgbDropdownModule } from '@ng-bootstrap/ng-bootstrap'
import { marked } from 'marked'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { filter, map } from 'rxjs'
import { ToastService } from 'src/app/services/toast.service'
import { ChatMessage, ChatService } from 'src/app/services/chat.service'

@Component({
  selector: 'pngx-chat',
  imports: [
    FormsModule,
    ReactiveFormsModule,
    NgxBootstrapIconsModule,
    NgbDropdownModule,
  ],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.scss',
})
export class ChatComponent implements OnInit {
  public messages: ChatMessage[] = []
  public loading = false
  public input: string = ''
  public documentId!: number

  private chatService: ChatService = inject(ChatService)
  private router: Router = inject(Router)
  private sanitizer: DomSanitizer = inject(DomSanitizer)
  private toastService: ToastService = inject(ToastService)
  private zone: NgZone = inject(NgZone)
  private cdr: ChangeDetectorRef = inject(ChangeDetectorRef)

  @ViewChild('scrollAnchor') scrollAnchor!: ElementRef<HTMLDivElement>
  @ViewChild('chatInput') chatInput!: ElementRef<HTMLInputElement>
  @ViewChild(NgbDropdown) private dropdown!: NgbDropdown

  public open(): void {
    this.dropdown?.open()
  }

  // Text received but not yet shown. It is revealed a few characters per
  // animation frame: one timer tick and one page-wide change detection per
  // character froze the whole dashboard while an answer was arriving.
  private typewriterBuffer = ''
  private typewriterActive = false
  private streamDone = false
  private scrollPending = false
  private markdownCache = new WeakMap<
    ChatMessage,
    { content: string; html: SafeHtml }
  >()

  public get placeholder(): string {
    return this.documentId
      ? $localize`Ask a question about this document...`
      : $localize`Ask a question about a document...`
  }

  ngOnInit(): void {
    this.updateDocumentId(this.router.url)
    this.router.events
      .pipe(
        filter((event) => event instanceof NavigationEnd),
        map((event) => (event as NavigationEnd).url)
      )
      .subscribe((url) => {
        this.updateDocumentId(url)
      })
  }

  private updateDocumentId(url: string): void {
    const docIdRe = url.match(/^\/documents\/(\d+)/)
    this.documentId = docIdRe ? +docIdRe[1] : undefined
  }

  sendMessage(): void {
    if (!this.input.trim()) return

    const userMessage: ChatMessage = { role: 'user', content: this.input }
    this.messages.push(userMessage)
    this.scrollToBottom()

    const assistantMessage: ChatMessage = {
      role: 'assistant',
      content: '',
      isStreaming: true,
    }
    this.messages.push(assistantMessage)
    this.loading = true

    let lastPartialLength = 0
    this.streamDone = false

    this.chatService.streamChat(this.documentId, this.input).subscribe({
      next: (chunk) => {
        const delta = chunk.substring(lastPartialLength)
        lastPartialLength = chunk.length
        this.enqueueTypewriter(delta, assistantMessage)
      },
      error: () => {
        this.typewriterBuffer = ''
        assistantMessage.content += '\n\n⚠️ Error receiving response.'
        assistantMessage.isStreaming = false
        this.loading = false
        this.render()
      },
      complete: () => {
        this.streamDone = true
        this.loading = false
        this.finishIfDrained(assistantMessage)
      },
    })

    this.input = ''
  }

  enqueueTypewriter(chunk: string, message: ChatMessage): void {
    if (!chunk) return

    this.typewriterBuffer += chunk

    if (!this.typewriterActive) {
      this.typewriterActive = true
      this.zone.runOutsideAngular(() =>
        requestAnimationFrame(() => this.playTypewriter(message))
      )
    }
  }

  playTypewriter(message: ChatMessage): void {
    if (this.typewriterBuffer.length === 0) {
      this.typewriterActive = false
      this.finishIfDrained(message)
      return
    }

    // A few characters per frame, more when far behind so long answers finish
    // revealing shortly after the last chunk arrives.
    const count = Math.max(3, Math.ceil(this.typewriterBuffer.length / 20))
    message.content += this.typewriterBuffer.slice(0, count)
    this.typewriterBuffer = this.typewriterBuffer.slice(count)
    this.render()

    requestAnimationFrame(() => this.playTypewriter(message))
  }

  private finishIfDrained(message: ChatMessage): void {
    if (!this.streamDone || this.typewriterActive || this.typewriterBuffer) {
      return
    }
    message.isStreaming = false
    this.render()
  }

  // Refresh only this component (not the whole application) and follow the
  // newest text.
  private render(): void {
    this.cdr.detectChanges()
    this.scrollToBottom()
  }

  // At most one scroll per frame, and not an animated one: a smooth scroll
  // started for every character kept restarting itself.
  private scrollToBottom(): void {
    if (this.scrollPending) return
    this.scrollPending = true
    this.zone.runOutsideAngular(() =>
      requestAnimationFrame(() => {
        this.scrollPending = false
        this.scrollAnchor?.nativeElement?.scrollIntoView({ behavior: 'auto' })
      })
    )
  }

  public onOpenChange(open: boolean): void {
    if (open) {
      setTimeout(() => {
        this.chatInput.nativeElement.focus()
      }, 10)
    }
  }

  public searchInputKeyDown(event: KeyboardEvent) {
    if (event.key === 'Enter') {
      event.preventDefault()
      this.sendMessage()
    }
  }

  // Called from the template on every change detection pass. Returning the
  // same object for unchanged text keeps Angular from re-parsing the markdown
  // and rebuilding the DOM each time.
  public renderMarkdown(message: ChatMessage): SafeHtml {
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

  public copyMessage(message: ChatMessage): void {
    navigator.clipboard
      .writeText(message.content)
      .then(() => {
        this.toastService.showInfo($localize`Copied response to clipboard`)
      })
      .catch(() => {
        this.toastService.showError($localize`Unable to copy response`)
      })
  }
}
