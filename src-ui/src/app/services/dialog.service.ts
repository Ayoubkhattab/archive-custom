import { Injectable, inject } from '@angular/core'
import { NgbModal, NgbModalRef } from '@ng-bootstrap/ng-bootstrap'
import {
  AppDialogComponent,
  DialogKind,
  DialogTone,
} from '../components/common/app-dialog/app-dialog.component'

export interface DialogOptions {
  title: string
  message?: string
  tone?: DialogTone
  /** Overrides the tone's icon (a bootstrap-icons name). */
  icon?: string
  confirmLabel?: string
  cancelLabel?: string
}

export interface PromptOptions extends DialogOptions {
  value?: string
  placeholder?: string
  inputLabel?: string
  multiline?: boolean
  /** The confirm button stays disabled until something is typed. */
  required?: boolean
  maxLength?: number
}

/**
 * Designed, non-blocking replacements for window.alert / confirm / prompt.
 * Never use the browser's own: they ignore the app's language, direction and
 * theme, show the site's address as their title, and freeze the page.
 *
 *   if (await this.dialogService.confirm({ title, message, tone: 'danger' })) …
 */
@Injectable({
  providedIn: 'root',
})
export class DialogService {
  private modalService = inject(NgbModal)

  /** Resolves when the dialog is closed, however it is closed. */
  alert(options: DialogOptions): Promise<void> {
    return this.open('alert', {
      confirmLabel: $localize`:@@dialog.ok:OK`,
      ...options,
    }).result.then(
      () => undefined,
      () => undefined
    )
  }

  /** True only when the person explicitly confirms. */
  confirm(options: DialogOptions): Promise<boolean> {
    return this.open('confirm', {
      confirmLabel: $localize`Confirm`,
      ...options,
    }).result.then(
      (result) => result === true,
      () => false
    )
  }

  /** The text entered, or null when cancelled. */
  prompt(options: PromptOptions): Promise<string | null> {
    return this.open('prompt', {
      confirmLabel: $localize`:@@dialog.ok:OK`,
      ...options,
    }).result.then(
      (result) => (typeof result === 'string' ? result : null),
      () => null
    )
  }

  private open(kind: DialogKind, options: PromptOptions): NgbModalRef {
    const ref = this.modalService.open(AppDialogComponent, {
      centered: true,
      modalDialogClass: 'pngx-app-dialog',
      ariaLabelledBy: 'app-dialog-title',
      ariaDescribedBy: options.message ? 'app-dialog-message' : undefined,
      // A decision is required; a stray click beside the dialog must not
      // count as one. Escape still cancels.
      backdrop: kind === 'alert' ? true : 'static',
    })
    const dialog = ref.componentInstance as AppDialogComponent
    dialog.kind = kind
    dialog.tone = options.tone ?? (kind === 'prompt' ? 'info' : 'warning')
    dialog.icon = options.icon
    dialog.title = options.title
    dialog.message = options.message
    dialog.confirmLabel = options.confirmLabel
    dialog.cancelLabel = options.cancelLabel ?? $localize`Cancel`
    dialog.value = options.value ?? ''
    dialog.placeholder = options.placeholder ?? ''
    dialog.inputLabel = options.inputLabel
    dialog.multiline = options.multiline ?? false
    dialog.required = options.required ?? false
    dialog.maxLength = options.maxLength
    return ref
  }
}
