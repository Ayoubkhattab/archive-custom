import { Component, inject } from '@angular/core'
import {
  FormControl,
  FormGroup,
  FormsModule,
  ReactiveFormsModule,
} from '@angular/forms'
import { EditDialogComponent } from 'src/app/components/common/edit-dialog/edit-dialog.component'
import {
  Correspondent,
  ENTITY_TYPE_LABELS,
  EntityType,
} from 'src/app/data/correspondent'
import { DEFAULT_MATCHING_ALGORITHM } from 'src/app/data/matching-model'
import { IfOwnerDirective } from 'src/app/directives/if-owner.directive'
import { CorrespondentService } from 'src/app/services/rest/correspondent.service'
import { UserService } from 'src/app/services/rest/user.service'
import { SettingsService } from 'src/app/services/settings.service'
import { CheckComponent } from '../../input/check/check.component'
import { PermissionsFormComponent } from '../../input/permissions/permissions-form/permissions-form.component'
import { SelectComponent } from '../../input/select/select.component'
import { TextComponent } from '../../input/text/text.component'

@Component({
  selector: 'pngx-correspondent-edit-dialog',
  templateUrl: './correspondent-edit-dialog.component.html',
  styleUrls: ['./correspondent-edit-dialog.component.scss'],
  imports: [
    CheckComponent,
    SelectComponent,
    PermissionsFormComponent,
    TextComponent,
    IfOwnerDirective,
    FormsModule,
    ReactiveFormsModule,
  ],
})
export class CorrespondentEditDialogComponent extends EditDialogComponent<Correspondent> {
  constructor() {
    super()
    this.service = inject(CorrespondentService)
    this.userService = inject(UserService)
    this.settingsService = inject(SettingsService)
  }

  getCreateTitle() {
    return $localize`Create new correspondent`
  }

  getEditTitle() {
    return $localize`Edit correspondent`
  }

  public entityTypes = Object.values(EntityType).map((id) => ({
    id,
    name: ENTITY_TYPE_LABELS[id],
  }))

  getForm(): FormGroup {
    return new FormGroup({
      name: new FormControl(''),
      code: new FormControl(''),
      diwan_number: new FormControl(''),
      entity_type: new FormControl(EntityType.External),
      matching_algorithm: new FormControl(DEFAULT_MATCHING_ALGORITHM),
      match: new FormControl(''),
      is_insensitive: new FormControl(true),
      permissions_form: new FormControl(null),
    })
  }
}
