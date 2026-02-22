import { KeyValuePipe } from '@angular/common'
import { Component, forwardRef, inject, Input, OnInit } from '@angular/core'
import {
  AbstractControl,
  ControlValueAccessor,
  FormControl,
  FormGroup,
  FormsModule,
  NG_VALUE_ACCESSOR,
  ReactiveFormsModule,
} from '@angular/forms'
import { NgbPopoverModule } from '@ng-bootstrap/ng-bootstrap'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { SETTINGS_KEYS } from 'src/app/data/ui-settings'
import {
  PermissionAction,
  PermissionsService,
  PermissionType,
} from 'src/app/services/permissions.service'
import { SettingsService } from 'src/app/services/settings.service'
import { ComponentWithPermissions } from '../../with-permissions/with-permissions.component'

@Component({
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => PermissionsSelectComponent),
      multi: true,
    },
  ],
  selector: 'pngx-permissions-select',
  templateUrl: './permissions-select.component.html',
  styleUrls: ['./permissions-select.component.scss'],
  imports: [
    KeyValuePipe,
    NgxBootstrapIconsModule,
    NgbPopoverModule,
    FormsModule,
    ReactiveFormsModule,
  ],
})
export class PermissionsSelectComponent
  extends ComponentWithPermissions
  implements OnInit, ControlValueAccessor
{
  private readonly permissionsService = inject(PermissionsService)
  private readonly settingsService = inject(SettingsService)

  @Input()
  title: string = 'Permissions'

  @Input()
  error: string

  permissions: string[]

  form = new FormGroup({})

  typesWithAllActions: Set<string> = new Set()

  _inheritedPermissions: string[] = []
  private _editorPermissions: string[] | null = null
  private _lockedPermissions: string[] = []
  lockedWarning: string = $localize`Requires higher privilege level`

  @Input()
  set editorPermissions(perms: string[] | null) {
    this._editorPermissions = perms
    this._computeLockedPermissions()
    this.updateDisabledStates()
  }

  @Input()
  set inheritedPermissions(inherited: string[]) {
    // remove <app_label>. from permission strings
    const newInheritedPermissions = inherited?.length
      ? inherited.map((p) => p.replace(/^\w+\./g, ''))
      : []

    if (this._inheritedPermissions !== newInheritedPermissions) {
      this._inheritedPermissions = newInheritedPermissions
      this.writeValue(this.permissions) // updates visual checks etc.
    }

    this.updateDisabledStates()
  }

  inheritedWarning: string = $localize`Inherited from group`

  public allowedTypes = Object.keys(PermissionType)

  constructor() {
    super()
    if (!this.settingsService.get(SETTINGS_KEYS.AUDITLOG_ENABLED)) {
      this.allowedTypes.splice(this.allowedTypes.indexOf('History'), 1)
    }
    this.allowedTypes.forEach((type) => {
      const control = new FormGroup({})
      for (const action in PermissionAction) {
        control.addControl(action, new FormControl(null))
      }
      this.form.addControl(type, control)
    })
  }

  writeValue(permissions: string[]): void {
    if (this.permissions === permissions) {
      return
    }

    this.permissions = permissions ?? []
    this._computeLockedPermissions()
    const allPerms = this._inheritedPermissions.concat(this.permissions)

    allPerms.forEach((permissionStr) => {
      const { actionKey, typeKey } =
        this.permissionsService.getPermissionKeys(permissionStr)

      if (actionKey && typeKey) {
        if (this.form.get(typeKey)?.get(actionKey)) {
          this.form
            .get(typeKey)
            .get(actionKey)
            .patchValue(true, { emitEvent: false })
        }
      }
    })
    this.allowedTypes.forEach((type) => {
      if (
        Object.values(this.form.get(type).value).every((val) => val == true)
      ) {
        this.typesWithAllActions.add(type)
      } else {
        this.typesWithAllActions.delete(type)
      }
    })

    this.updateDisabledStates()
  }

  onChange = (newValue: string[]) => {}

  onTouched = () => {}

  disabled: boolean = false

  registerOnChange(fn: any): void {
    this.onChange = fn
  }

  registerOnTouched(fn: any): void {
    this.onTouched = fn
  }

  setDisabledState?(isDisabled: boolean): void {
    this.disabled = isDisabled
  }

  ngOnInit(): void {
    this.form.valueChanges.subscribe((newValue) => {
      let permissions = []
      Object.entries(newValue).forEach(([typeKey, typeValue]) => {
        // e.g. [Document, { Add: true, View: true ... }]
        const selectedActions = Object.entries(typeValue).filter(
          ([actionKey, actionValue]) => actionValue == true
        )

        selectedActions.forEach(([actionKey, actionValue]) => {
          permissions.push(
            (PermissionType[typeKey] as string).replace(
              '%s',
              PermissionAction[actionKey]
            )
          )
        })

        if (selectedActions.length == Object.entries(typeValue).length) {
          this.typesWithAllActions.add(typeKey)
        } else {
          this.typesWithAllActions.delete(typeKey)
        }
      })

      this.onChange(
        [
          ...permissions.filter((p) => !this._inheritedPermissions.includes(p)),
          ...this._lockedPermissions,
        ]
      )
    })
  }

  toggleAll(event, type) {
    const typeGroup = this.form.get(type)
    if (event.target.checked) {
      Object.keys(PermissionAction).forEach((action) => {
        typeGroup.get(action).patchValue(true)
      })
      this.typesWithAllActions.add(type)
    } else {
      Object.keys(PermissionAction).forEach((action) => {
        typeGroup.get(action).patchValue(false)
      })
      this.typesWithAllActions.delete(type)
    }
  }

  isInherited(typeKey: string, actionKey: string = null) {
    if (this._inheritedPermissions.length == 0) return false
    else if (actionKey) {
      return this._inheritedPermissions.includes(
        this.permissionsService.getPermissionCode(
          PermissionAction[actionKey],
          PermissionType[typeKey]
        )
      )
    } else {
      return Object.values(PermissionAction).every((action) => {
        return this._inheritedPermissions.includes(
          this.permissionsService.getPermissionCode(
            action as PermissionAction,
            PermissionType[typeKey]
          )
        )
      })
    }
  }

  isLocked(typeKey: string, actionKey: string): boolean {
    if (this._editorPermissions === null) return false
    const permCode = this.permissionsService.getPermissionCode(
      PermissionAction[actionKey],
      PermissionType[typeKey]
    )
    return !this._editorPermissions.includes(permCode)
  }

  isAnyLocked(typeKey: string): boolean {
    if (this._editorPermissions === null) return false
    return Object.keys(PermissionAction).some((action) => this.isLocked(typeKey, action))
  }

  private _computeLockedPermissions(): void {
    if (this._editorPermissions === null || !this.permissions?.length) {
      this._lockedPermissions = []
      return
    }
    this._lockedPermissions = this.permissions.filter(
      (p) =>
        !this._editorPermissions.includes(p) &&
        !this._inheritedPermissions.includes(p)
    )
  }

  updateDisabledStates() {
    this.allowedTypes.forEach((type) => {
      const control = this.form.get(type)
      for (const action in PermissionAction) {
        const actionControl = control.get(action)
        const permCode = this.permissionsService.getPermissionCode(
          PermissionAction[action],
          PermissionType[type]
        )
        const isInheritedPerm = this.isInherited(type, action)
        const isLockedPerm =
          this._editorPermissions !== null &&
          !this._editorPermissions.includes(permCode)

        isInheritedPerm || isLockedPerm || this.disabled
          ? actionControl.disable()
          : actionControl.enable()
      }
    })
  }
}
