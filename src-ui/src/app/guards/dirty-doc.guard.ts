import { Injectable, inject } from '@angular/core'
import { Observable, from } from 'rxjs'
import { DialogService } from '../services/dialog.service'

export interface ComponentCanDeactivate {
  canDeactivate: () => boolean | Observable<boolean>
}

@Injectable()
export class DirtyDocGuard {
  private dialogService = inject(DialogService)

  canDeactivate(
    component: ComponentCanDeactivate
  ): boolean | Observable<boolean> {
    return component.canDeactivate()
      ? true
      : from(
          this.dialogService.confirm({
            title: $localize`Unsaved Changes`,
            message: $localize`Warning: You have unsaved changes to your document(s).`,
            tone: 'warning',
            confirmLabel: $localize`:@@dialog.leaveWithoutSaving:Leave without saving`,
            cancelLabel: $localize`:@@dialog.stayOnPage:Stay on this page`,
          })
        )
  }
}
