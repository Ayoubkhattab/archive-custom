import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  EventEmitter,
  inject,
  Input,
  NgZone,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
} from '@angular/core'
import {
  HttpClient,
  HttpErrorResponse,
  HttpEventType,
} from '@angular/common/http'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { PDFDocumentProxy, PdfViewerModule } from 'ng2-pdf-viewer'
import { Subscription, throwError, timer, TimeoutError } from 'rxjs'
import { filter, retry, timeout } from 'rxjs/operators'

/**
 * States of the viewer. The UI is only ever "loading"/"rendering" while a
 * timer is running, so it can never get stuck on "Loading...".
 */
export type PdfViewerState =
  | 'idle'
  | 'loading'
  | 'rendering'
  | 'success'
  | 'password'
  | 'error'

export type PdfErrorKind =
  | 'timeout'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'range'
  | 'server'
  | 'network'
  | 'corrupted'
  | 'empty'
  | 'encrypted'
  | 'wrong_password'
  | 'unknown'

export const PDF_ERROR_MESSAGES: Record<PdfErrorKind, string> = {
  timeout: $localize`Loading the PDF took too long.`,
  unauthorized: $localize`Your session has expired. Please sign in again.`,
  forbidden: $localize`You do not have permission to view this PDF.`,
  not_found: $localize`This PDF could not be found.`,
  range: $localize`The server could not deliver the requested part of this PDF.`,
  server: $localize`The server failed to deliver this PDF.`,
  network: $localize`Network error while loading the PDF.`,
  corrupted: $localize`This PDF file is damaged and cannot be displayed.`,
  empty: $localize`This PDF file is empty.`,
  encrypted: $localize`This PDF is password protected.`,
  wrong_password: $localize`The password is incorrect.`,
  unknown: $localize`The PDF could not be displayed.`,
}

export function errorKindForStatus(status: number): PdfErrorKind {
  switch (status) {
    case 0:
      return 'network'
    case 401:
      return 'unauthorized'
    case 403:
      return 'forbidden'
    case 404:
      return 'not_found'
    case 416:
      return 'range'
    default:
      return status >= 500 ? 'server' : 'unknown'
  }
}

/** A PDF starts with "%PDF-" (some writers put junk before it, allow 1 KiB). */
export function looksLikePdf(buffer: ArrayBuffer): boolean {
  const head = new Uint8Array(buffer, 0, Math.min(1024, buffer.byteLength))
  const text = String.fromCharCode(...head)
  return text.includes('%PDF-')
}

@Component({
  selector: 'pngx-pdf-viewer',
  templateUrl: './pdf-viewer.component.html',
  styleUrls: ['./pdf-viewer.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [PdfViewerModule, NgxBootstrapIconsModule],
})
export class PdfViewerComponent implements OnChanges, OnDestroy {
  private http = inject(HttpClient)
  private cdr = inject(ChangeDetectorRef)
  private zone = inject(NgZone)

  /** Authenticated API URL of the PDF (sent with the session cookie). */
  @Input() src: string
  @Input() password: string
  @Input() downloadUrl: string
  /** Inactivity timeout for the download, and timeout for rendering. */
  @Input() timeoutMs = 20000

  // Pass-through options for the renderer (ng2-pdf-viewer / PDF.js).
  @Input() page = 1
  @Output() pageChange = new EventEmitter<number>()
  @Input() zoom = 1
  @Input() zoomScale: 'page-width' | 'page-fit' | 'page-height' = 'page-width'
  @Input() showAll = true
  @Input() showBorders = true
  @Input() originalSize = false
  @Input() renderText = true

  @Output() loaded = new EventEmitter<PDFDocumentProxy>()
  @Output() stateChange = new EventEmitter<PdfViewerState>()
  @Output() passwordRequired = new EventEmitter<void>()

  state: PdfViewerState = 'idle'
  errorKind: PdfErrorKind = null
  /** 0..100 while downloading, null when the size is unknown. */
  progress: number = null
  pdfSrc: { data: Uint8Array; password?: string } = null

  private buffer: ArrayBuffer = null
  private pdf: PDFDocumentProxy = null
  private request: Subscription = null
  private renderTimer: ReturnType<typeof setTimeout> = null
  private objectUrls: string[] = []
  private blobUrl: string = null

  get errorMessage(): string {
    return this.errorKind ? PDF_ERROR_MESSAGES[this.errorKind] : ''
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['src']) {
      this.load()
    } else if (changes['password'] && this.buffer) {
      this.render()
    }
  }

  retry(): void {
    this.load()
  }

  load(): void {
    this.cleanup()
    this.objectUrls.forEach((url) => URL.revokeObjectURL(url))
    this.objectUrls = []
    this.blobUrl = null
    this.buffer = null
    this.pdfSrc = null
    this.progress = null
    if (!this.src) {
      this.setState('idle')
      return
    }
    this.setState('loading')

    this.request = this.http
      .get(this.src, {
        responseType: 'arraybuffer',
        observe: 'events',
        reportProgress: true,
      })
      .pipe(
        // Inactivity timeout: every progress event restarts the clock, so a
        // large PDF on a slow line still loads, a stalled one fails.
        timeout({ each: this.timeoutMs }),
        // A 401 is retried once (the session may have just been renewed in
        // another tab); anything else fails straight away.
        retry({
          count: 1,
          delay: (error) =>
            error instanceof HttpErrorResponse && error.status === 401
              ? timer(0)
              : throwError(() => error),
        })
      )
      .subscribe({
        next: (event) => {
          if (event.type === HttpEventType.DownloadProgress) {
            this.progress = event.total
              ? Math.round((100 * event.loaded) / event.total)
              : null
            this.cdr.markForCheck()
          } else if (event.type === HttpEventType.Response) {
            this.onDownloaded(event.body)
          }
        },
        error: (error) => {
          if (error instanceof TimeoutError) this.fail('timeout')
          else if (error instanceof HttpErrorResponse)
            this.fail(errorKindForStatus(error.status))
          else this.fail('network')
        },
      })
  }

  private onDownloaded(body: ArrayBuffer): void {
    if (!body || body.byteLength === 0) return this.fail('empty')
    if (!looksLikePdf(body)) return this.fail('corrupted')
    this.buffer = body
    this.render()
  }

  private render(): void {
    this.clearRenderTimer()
    this.destroyPdf()
    this.setState('rendering')
    // PDF.js transfers (detaches) the buffer it gets, so hand it a copy and
    // keep the original for password retries and the download fallback.
    this.pdfSrc = {
      data: new Uint8Array(this.buffer.slice(0)),
      password: this.password || undefined,
    }
    this.zone.runOutsideAngular(() => {
      this.renderTimer = setTimeout(
        () => this.zone.run(() => this.fail('timeout')),
        this.timeoutMs
      )
    })
  }

  onLoadComplete(pdf: PDFDocumentProxy): void {
    this.clearRenderTimer()
    this.pdf = pdf
    if (!pdf?.numPages) return this.fail('empty')
    this.errorKind = null
    this.setState('success')
    this.loaded.emit(pdf)
  }

  onRenderError(error: any): void {
    this.clearRenderTimer()
    const name = error?.name
    if (name === 'PasswordException') {
      // code 2 = INCORRECT_PASSWORD, 1 = NEED_PASSWORD
      this.errorKind = error.code === 2 ? 'wrong_password' : 'encrypted'
      this.pdfSrc = null
      this.setState('password')
      this.passwordRequired.emit()
      return
    }
    this.fail(
      name === 'InvalidPDFException' || name === 'FormatError'
        ? 'corrupted'
        : name === 'MissingPDFException'
          ? 'not_found'
          : 'unknown'
    )
  }

  /** Download fallback: the API URL if given, otherwise the fetched bytes. */
  get fallbackUrl(): string {
    if (this.downloadUrl) return this.downloadUrl
    if (!this.buffer) return null
    if (!this.blobUrl) {
      this.blobUrl = URL.createObjectURL(
        new Blob([this.buffer], { type: 'application/pdf' })
      )
      this.objectUrls.push(this.blobUrl)
    }
    return this.blobUrl
  }

  private fail(kind: PdfErrorKind): void {
    this.cleanup()
    this.pdfSrc = null
    this.errorKind = kind
    this.setState('error')
  }

  private setState(state: PdfViewerState): void {
    this.state = state
    this.stateChange.emit(state)
    this.cdr.markForCheck()
  }

  private clearRenderTimer(): void {
    if (this.renderTimer) clearTimeout(this.renderTimer)
    this.renderTimer = null
  }

  private destroyPdf(): void {
    if (this.pdf) {
      this.pdf.destroy()
      this.pdf = null
    }
  }

  private cleanup(): void {
    // Unsubscribing aborts the HTTP request (AbortController under fetch).
    this.request?.unsubscribe()
    this.request = null
    this.clearRenderTimer()
    this.destroyPdf()
  }

  ngOnDestroy(): void {
    this.cleanup()
    this.objectUrls.forEach((url) => URL.revokeObjectURL(url))
    this.objectUrls = []
    this.blobUrl = null
    this.buffer = null
  }
}
