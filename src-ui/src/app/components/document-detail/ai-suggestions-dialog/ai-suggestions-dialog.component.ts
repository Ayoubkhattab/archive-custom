import { Component, EventEmitter, OnDestroy, OnInit, inject } from '@angular/core'
import { NgbActiveModal } from '@ng-bootstrap/ng-bootstrap'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { Subscription } from 'rxjs'
import {
  AiClassification,
  AiSuggestionService,
} from 'src/app/services/ai-suggestion.service'
import { ToastService } from 'src/app/services/toast.service'

interface SuggestionRow {
  key: keyof AiClassification
  icon: string
  label: string
  values: string[]
}

/**
 * What the AI suggests for a document, shown as a readable summary instead
 * of raw JSON in a toast, with an optional "apply" for people who may edit.
 */
@Component({
  selector: 'pngx-ai-suggestions-dialog',
  templateUrl: './ai-suggestions-dialog.component.html',
  styleUrl: './ai-suggestions-dialog.component.scss',
  imports: [NgxBootstrapIconsModule],
})
export class AiSuggestionsDialogComponent implements OnInit, OnDestroy {
  activeModal = inject(NgbActiveModal)
  private aiSuggestionService = inject(AiSuggestionService)
  private toastService = inject(ToastService)

  documentId: number
  documentTitle: string
  canApply = false
  /** Applying saves on the server; local unsaved edits would conflict. */
  hasUnsavedChanges = false

  /** Emits after the suggestions were applied and saved on the server. */
  applied = new EventEmitter<void>()

  state: 'loading' | 'error' | 'ready' | 'applying' = 'loading'
  error: string
  cached = false
  rows: SuggestionRow[] = []
  elapsed = 0

  private timer: ReturnType<typeof setInterval>
  private request: Subscription

  ngOnInit(): void {
    this.load()
  }

  ngOnDestroy(): void {
    this.stopTimer()
    this.request?.unsubscribe()
  }

  get isEmpty(): boolean {
    return this.rows.length === 0
  }

  load() {
    this.state = 'loading'
    this.error = null
    this.startTimer()
    this.request?.unsubscribe()
    this.request = this.aiSuggestionService
      .classify(this.documentId, false)
      .subscribe({
        next: (result) => {
          this.stopTimer()
          this.cached = result.cached
          this.rows = this.toRows(result.classification)
          this.state = 'ready'
        },
        error: (error: Error) => {
          this.stopTimer()
          this.error = error.message
          this.state = 'error'
        },
      })
  }

  apply() {
    if (!this.canApply || this.hasUnsavedChanges || this.state !== 'ready') {
      return
    }
    this.state = 'applying'
    this.request = this.aiSuggestionService
      .classify(this.documentId, true)
      .subscribe({
        next: () => {
          this.toastService.showInfo(
            $localize`:@@aiSuggest.applied:AI suggestions applied and saved.`
          )
          this.applied.emit()
          this.activeModal.close(true)
        },
        error: (error: Error) => {
          this.error = error.message
          this.state = 'error'
        },
      })
  }

  copy(text: string) {
    navigator.clipboard
      ?.writeText(text)
      .then(() =>
        this.toastService.showInfo($localize`:@@ask.copied:Copied to clipboard`)
      )
  }

  close() {
    this.activeModal.dismiss()
  }

  private toRows(c: AiClassification): SuggestionRow[] {
    const clean = (values: unknown): string[] =>
      (Array.isArray(values) ? values : [])
        .filter((v) => typeof v === 'string' && v.trim())
        .map((v: string) => v.trim())
    const rows: SuggestionRow[] = [
      {
        key: 'title',
        icon: 'card-heading',
        label: $localize`Title`,
        values: c.title?.trim() ? [c.title.trim()] : [],
      },
      {
        key: 'tags',
        icon: 'tags',
        label: $localize`Tags`,
        values: clean(c.tags),
      },
      {
        key: 'correspondents',
        icon: 'person',
        label: $localize`Correspondent`,
        values: clean(c.correspondents),
      },
      {
        key: 'document_types',
        icon: 'hash',
        label: $localize`Document type`,
        values: clean(c.document_types),
      },
      {
        key: 'storage_paths',
        icon: 'folder',
        label: $localize`Storage path`,
        values: clean(c.storage_paths),
      },
      {
        key: 'dates',
        icon: 'calendar',
        label: $localize`:@@aiSuggest.dates:Dates`,
        values: clean(c.dates),
      },
    ]
    return rows.filter((row) => row.values.length)
  }

  private startTimer() {
    this.stopTimer()
    this.elapsed = 0
    this.timer = setInterval(() => this.elapsed++, 1000)
  }

  private stopTimer() {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  }
}
