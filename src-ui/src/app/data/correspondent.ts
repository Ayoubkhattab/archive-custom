import { MatchingModel } from './matching-model'

export enum EntityType {
  External = 'external',
  Internal = 'internal',
}

export const ENTITY_TYPE_LABELS = {
  [EntityType.External]: $localize`External entity`,
  [EntityType.Internal]: $localize`Internal department`,
}

export interface Correspondent extends MatchingModel {
  last_correspondence?: string // Date

  // Official identification
  code?: string
  diwan_number?: string
  entity_type?: EntityType

  // Read-only counts
  sent_document_count?: number
  received_document_count?: number
}
