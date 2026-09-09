import { EntityType } from './correspondent'

/** Which document date a correspondence report is scoped to. */
export enum ReportDateField {
  Added = 'added',
  Created = 'created',
  Sent = 'sent_date',
  Returned = 'returned_date',
}

export const REPORT_DATE_FIELD_LABELS = {
  [ReportDateField.Added]: $localize`Upload date`,
  [ReportDateField.Created]: $localize`Document date`,
  [ReportDateField.Sent]: $localize`Sent date`,
  [ReportDateField.Returned]: $localize`Returned date`,
}

export interface TurnaroundStats {
  routed_count: number
  returned_count: number
  awaiting_return_count: number
  bottlenecked_count: number
  total_bottleneck_days: number
  average_turnaround_days: number | null
  average_bottleneck_days: number | null
}

export interface EntityAnalytics extends TurnaroundStats {
  id: number
  name: string
  code: string | null
  diwan_number: string
  entity_type: EntityType

  /** Documents this entity sent out. */
  sent_count: number
  /** Documents this entity received. */
  received_count: number
  /** Documents filed against this entity as correspondent. */
  uploaded_count: number
}

export interface MonthlyPoint {
  month: string // YYYY-MM
  sent: number
  received: number
  uploaded: number
}

export interface NamedCount {
  id: number | null
  name: string | null
  count: number
}

export interface CorrespondenceAnalytics {
  period: {
    date_from: string
    date_to: string
    date_field: ReportDateField
  }
  /** Tolerated days before bottleneck days start accruing. */
  grace_days: number
  totals: TurnaroundStats & { documents: number }
  entities: EntityAnalytics[]
  monthly: MonthlyPoint[]
  by_document_type: NamedCount[]
  by_classification: NamedCount[]
}
