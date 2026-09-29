import {
  AfterViewInit,
  Component,
  ElementRef,
  ViewChild,
  inject,
} from '@angular/core'
import { FormsModule } from '@angular/forms'
import { NgbActiveModal } from '@ng-bootstrap/ng-bootstrap'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'

export type DialogTone = 'info' | 'success' | 'warning' | 'danger'
export type DialogKind = 'alert' | 'confirm' | 'prompt'

const TONE_ICONS: Record<DialogTone, string> = {
  info: 'info-circle',
  success: 'check-circle',
  warning: 'exclamation-triangle',
  danger: 'exclamation-triangle',
}

/**
 * The app's replacement for the browser's alert(), confirm() and prompt():
 * same questions, but in the app's design, direction and language, and
 * without blocking the page. Opened through DialogService, which resolves
 * the result.
 */
@Component({
  selector: 'pngx-app-dialog',
  templateUrl: './app-dialog.component.html',
  imports: [FormsModule, NgxBootstrapIconsModule],
})
export class AppDialogComponent implements AfterViewInit {
  activeModal = inject(NgbActiveModal)

  kind: DialogKind = 'confirm'
  tone: DialogTone = 'info'
  icon: string
  title: string
  message: string
  confirmLabel: string
  cancelLabel: string

  // prompt only
  value = ''
  placeholder = ''
  inputLabel: string
  multiline = false
  required = false
  maxLength: number

  @ViewChild('field') field: ElementRef<HTMLInputElement | HTMLTextAreaElement>
  @ViewChild('confirmButton') confirmButton: ElementRef<HTMLButtonElement>
  @ViewChild('cancelButton') cancelButton: ElementRef<HTMLButtonElement>

  get iconName(): string {
    return this.icon ?? TONE_ICONS[this.tone]
  }

  get confirmClass(): string {
    // Only destructive actions get their own colour; everything else uses the
    // identity's primary button (Bootstrap's yellow warning is off-palette).
    return this.tone === 'danger' ? 'btn-danger' : 'btn-primary'
  }

  get canConfirm(): boolean {
    return this.kind !== 'prompt' || !this.required || !!this.value?.trim()
  }

  ngAfterViewInit(): void {
    // Focus what the person is most likely to want: the field for a prompt,
    // the safe choice for a destructive confirmation, the main button otherwise.
    setTimeout(() => {
      if (this.kind === 'prompt') {
        this.field?.nativeElement.focus()
        this.field?.nativeElement.select()
      } else if (this.tone === 'danger' && this.kind === 'confirm') {
        this.cancelButton?.nativeElement.focus()
      } else {
        this.confirmButton?.nativeElement.focus()
      }
    })
  }

  confirm() {
    if (!this.canConfirm) return
    this.activeModal.close(this.kind === 'prompt' ? this.value : true)
  }

  cancel() {
    this.activeModal.dismiss('cancel')
  }

  onFieldKeydown(event: KeyboardEvent) {
    if (
      event.key === 'Enter' &&
      (!this.multiline || event.ctrlKey || event.metaKey) &&
      !event.isComposing
    ) {
      event.preventDefault()
      this.confirm()
    }
  }
}
