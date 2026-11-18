import { Injectable } from '@angular/core'
import { ObjectWithPermissions } from '../data/object-with-permissions'
import { User } from '../data/user'

export enum PermissionAction {
  Add = 'add',
  View = 'view',
  Change = 'change',
  Delete = 'delete',
}

export enum PermissionType {
  Document = '%s_document',
  Tag = '%s_tag',
  Correspondent = '%s_correspondent',
  DocumentType = '%s_documenttype',
  StoragePath = '%s_storagepath',
  SavedView = '%s_savedview',
  PaperlessTask = '%s_paperlesstask',
  AppConfig = '%s_applicationconfiguration',
  UISettings = '%s_uisettings',
  History = '%s_logentry',
  Note = '%s_note',
  MailAccount = '%s_mailaccount',
  MailRule = '%s_mailrule',
  User = '%s_user',
  Group = '%s_group',
  ShareLink = '%s_sharelink',
  CustomField = '%s_customfield',
  Workflow = '%s_workflow',
  ProcessedMail = '%s_processedmail',
}

/** Policy permission names, mirrored from paperless/access/constants.py. */
export type PolicyFamily =
  | 'user.read'
  | 'user.update'
  | 'user.delete'
  | 'pdf.view'

export const SYSTEM_MANAGE_ALL = 'system.manage.all'

@Injectable({
  providedIn: 'root',
})
export class PermissionsService {
  private _permissions: string[]
  private currentUser: User

  public get allPermissions(): string[] {
    return this._permissions
  }

  public initialize(permissions: string[], currentUser: User) {
    this._permissions = permissions
    this.currentUser = currentUser
  }

  public currentUserCan(
    action: PermissionAction,
    type: PermissionType
  ): boolean {
    return (
      this.isSystemAdmin() ||
      this._permissions?.includes(this.getPermissionCode(action, type))
    )
  }

  /** Holder of system.manage.all (or Django superuser). */
  public isSystemAdmin(): boolean {
    return !!(
      this.currentUser?.is_superuser || this.currentUser?.is_system_admin
    )
  }

  /** Is ``userId`` the parent, grandparent, ... of the current user? */
  public isAncestorOfCurrentUser(userId: number): boolean {
    return !!userId && (this.currentUser?.ancestors ?? []).includes(userId)
  }

  /**
   * UI mirror of the backend decision order (paperless/access/policy.py):
   * global admin -> self -> direct child -> deny descendant-to-ancestor ->
   * ".any" -> deny. Only used to hide controls; the server decides.
   *
   * @param target the account the action is about (owner of an image, the
   *               user being edited, the owner of a document)
   */
  public canOnTarget(
    family: PolicyFamily,
    target: { id?: number; parent?: number | null } | null
  ): boolean {
    const me = this.currentUser
    if (!me || !target?.id) return false
    const access = me.access ?? []
    const mutating = family !== 'user.read' && family !== 'pdf.view'
    const isSelf = target.id === me.id
    if (isSelf && family === 'user.delete') return false
    if (this.isSystemAdmin()) return true
    if (isSelf) return access.includes(`${family}.self`)
    if (target.parent === me.id && access.includes(`${family}.child`))
      return true
    if (mutating && this.isAncestorOfCurrentUser(target.id)) return false
    return access.includes(`${family}.any`)
  }

  /** Does the user hold the family in any scope (coarse gate, e.g. menus)? */
  public holdsAny(family: PolicyFamily): boolean {
    if (this.isSystemAdmin()) return true
    const access = this.currentUser?.access ?? []
    return ['self', 'child', 'any'].some((s) =>
      access.includes(`${family}.${s}`)
    )
  }

  public isAdmin(): boolean {
    return this.currentUser?.is_staff
  }

  public isSuperUser(): boolean {
    return this.currentUser?.is_superuser
  }

  public currentUserOwnsObject(object: ObjectWithPermissions): boolean {
    return (
      !object ||
      !object.owner ||
      this.isSystemAdmin() ||
      object.owner === this.currentUser.id
    )
  }

  public currentUserHasObjectPermissions(
    action: string,
    object: ObjectWithPermissions
  ): boolean {
    if (action === PermissionAction.View) {
      return (
        this.currentUserOwnsObject(object) ||
        object.permissions?.view.users.includes(this.currentUser.id) ||
        object.permissions?.view.groups.filter((g) =>
          this.currentUser.groups.includes(g)
        ).length > 0
      )
    } else if (action === PermissionAction.Change) {
      return (
        this.currentUserOwnsObject(object) ||
        object.user_can_change ||
        object.permissions?.change.users.includes(this.currentUser.id) ||
        object.permissions?.change.groups.filter((g) =>
          this.currentUser.groups.includes(g)
        ).length > 0
      )
    } else if (action === PermissionAction.Delete) {
      return (
        this.currentUserOwnsObject(object) ||
        object.user_can_delete ||
        object.permissions?.delete?.users.includes(this.currentUser.id) ||
        object.permissions?.delete?.groups.filter((g) =>
          this.currentUser.groups.includes(g)
        ).length > 0
      )
    }
  }

  public getPermissionCode(
    action: PermissionAction,
    type: PermissionType
  ): string {
    return type.replace('%s', action)
  }

  public getPermissionKeys(permissionStr: string): {
    actionKey: string
    typeKey: string
  } {
    const matches = permissionStr.match(/(.+)_/)
    let typeKey
    let actionKey
    if (matches?.length > 0) {
      const action = matches[1]
      const actionIndex = Object.values(PermissionAction).indexOf(
        action as PermissionAction
      )
      if (actionIndex > -1) {
        actionKey = Object.keys(PermissionAction)[actionIndex]
      }
      const typeIndex = Object.values(PermissionType).indexOf(
        permissionStr.replace(action, '%s') as PermissionType
      )
      if (typeIndex > -1) {
        typeKey = Object.keys(PermissionType)[typeIndex]
      }
    }

    return { actionKey, typeKey }
  }
}
