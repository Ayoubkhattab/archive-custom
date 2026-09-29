import { Injectable, inject } from '@angular/core'
import { NgbModal, NgbModalRef } from '@ng-bootstrap/ng-bootstrap'
import { CommandPaletteComponent } from '../components/app-frame/command-palette/command-palette.component'

/**
 * Opens the command palette (Ctrl/⌘+K): one keyboard-first entry point to
 * documents, saved views, pages and common actions.
 */
@Injectable({
  providedIn: 'root',
})
export class CommandPaletteService {
  private modalService = inject(NgbModal)
  private ref: NgbModalRef = null

  get isOpen(): boolean {
    return this.ref !== null
  }

  open(initialQuery: string = '') {
    if (this.ref) return
    this.ref = this.modalService.open(CommandPaletteComponent, {
      size: 'lg',
      modalDialogClass: 'pngx-palette-dialog',
      windowClass: 'pngx-palette-window',
      backdropClass: 'pngx-palette-backdrop',
      ariaLabelledBy: 'pngx-palette-title',
    })
    if (initialQuery) this.ref.componentInstance.setQuery(initialQuery)
    this.ref.hidden.subscribe(() => (this.ref = null))
  }

  toggle() {
    if (this.ref) {
      this.ref.dismiss()
      this.ref = null
    } else {
      this.open()
    }
  }
}
