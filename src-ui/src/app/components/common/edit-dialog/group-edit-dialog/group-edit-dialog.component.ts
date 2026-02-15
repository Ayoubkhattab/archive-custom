import { Component, OnInit, inject } from '@angular/core'
import {
  FormControl,
  FormGroup,
  FormsModule,
  ReactiveFormsModule,
} from '@angular/forms'
import { Observable, of } from 'rxjs'
import { AuditLogEntry } from 'src/app/data/auditlog-entry'
import { EditDialogComponent } from 'src/app/components/common/edit-dialog/edit-dialog.component'
import { Group } from 'src/app/data/group'
import { GroupService } from 'src/app/services/rest/group.service'
import { UserService } from 'src/app/services/rest/user.service'
import { SettingsService } from 'src/app/services/settings.service'
import { AuditHistoryComponent } from '../../audit-history/audit-history.component'
import { TextComponent } from '../../input/text/text.component'
import { PermissionsSelectComponent } from '../../permissions-select/permissions-select.component'

@Component({
  selector: 'pngx-group-edit-dialog',
  templateUrl: './group-edit-dialog.component.html',
  styleUrls: ['./group-edit-dialog.component.scss'],
  imports: [
    PermissionsSelectComponent,
    AuditHistoryComponent,
    TextComponent,
    FormsModule,
    ReactiveFormsModule,
  ],
})
export class GroupEditDialogComponent extends EditDialogComponent<Group> implements OnInit {
  public historyEntries$: Observable<AuditLogEntry[]> = of([])

  constructor() {
    super()
    this.service = inject(GroupService)
    this.userService = inject(UserService)
    this.settingsService = inject(SettingsService)
  }

  ngOnInit(): void {
    super.ngOnInit()
    if (this.object?.id) {
      this.historyEntries$ = (this.service as GroupService).getHistory(this.object.id)
    }
  }

  getCreateTitle() {
    return $localize`Create new user group`
  }

  getEditTitle() {
    return $localize`Edit user group`
  }

  getForm(): FormGroup {
    return new FormGroup({
      name: new FormControl(''),
      permissions: new FormControl([]),
    })
  }
}
