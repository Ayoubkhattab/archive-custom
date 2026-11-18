import { ObjectWithId } from './object-with-id'

export interface User extends ObjectWithId {
  username?: string
  first_name?: string
  last_name?: string
  date_joined?: Date
  is_staff?: boolean
  is_active?: boolean
  is_superuser?: boolean
  groups?: number[] // Group[]
  user_permissions?: string[]
  inherited_permissions?: string[]
  is_mfa_enabled?: boolean
  created_by_id?: number
  /** Parent ("father") account id in the hierarchy. */
  parent?: number | null

  // Only present on the current user (from /api/ui_settings/). They mirror
  // the server-side policy so forbidden controls can be hidden; the server
  // re-checks every request and never trusts these.
  /** Holder of system.manage.all (or superuser). */
  is_system_admin?: boolean
  /** Effective dotted policy permissions, e.g. "image.upload.self". */
  access?: string[]
  /** Parent, grandparent, ... of the current user. */
  ancestors?: number[]
}
