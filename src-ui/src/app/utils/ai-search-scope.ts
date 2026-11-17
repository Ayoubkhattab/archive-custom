import { Params } from '@angular/router'
import { FilterRule } from 'src/app/data/filter-rule'
import { FILTER_RULE_TYPES } from 'src/app/data/filter-rule-type'
import { queryParamsFromFilterRules } from './query-params'

/**
 * Which documents the AI page may draw an answer from.
 *
 * `filterRules` are those of the document list's filter editor (text, tags,
 * correspondent, document type, storage path, custom fields, dates, owner), so
 * they are sent as the same query parameters. The official-correspondence
 * fields have no rule type there and are kept beside them.
 */
export interface AiSearchScope {
  filterRules: FilterRule[]
  senders: number[]
  recipients: number[]
  classifications: number[]
}

export function emptyAiSearchScope(): AiSearchScope {
  return { filterRules: [], senders: [], recipients: [], classifications: [] }
}

/**
 * A copy that is safe to keep after the original is edited. Also used on what
 * comes back from browser storage, so anything malformed is dropped.
 */
export function cloneAiSearchScope(
  scope: AiSearchScope | undefined
): AiSearchScope {
  if (!scope || typeof scope !== 'object') return emptyAiSearchScope()
  const ids = (value: unknown): number[] =>
    Array.isArray(value) ? value.filter((id) => Number.isInteger(id)) : []
  const rules = Array.isArray(scope.filterRules) ? scope.filterRules : []
  return {
    filterRules: rules
      // A rule type this version does not know would break the conversion
      // into query parameters.
      .filter(
        (rule) => rule && FILTER_RULE_TYPES.some((t) => t.id === rule.rule_type)
      )
      .map((rule) => ({ ...rule })),
    senders: ids(scope.senders),
    recipients: ids(scope.recipients),
    classifications: ids(scope.classifications),
  }
}

/** The document-list query parameters the chat endpoint expects as `filters`. */
export function aiSearchScopeToParams(
  scope: AiSearchScope | undefined
): Params {
  if (!scope) return {}
  const params: Params = {
    ...(queryParamsFromFilterRules(scope.filterRules ?? []) ?? {}),
  }
  if (scope.senders?.length) params['sender__id__in'] = scope.senders.join(',')
  if (scope.recipients?.length)
    params['recipient__id__in'] = scope.recipients.join(',')
  if (scope.classifications?.length)
    params['classification__id__in'] = scope.classifications.join(',')
  return params
}

/** How many filters are set, counting each chosen field once. */
export function aiSearchScopeFilterCount(
  scope: AiSearchScope | undefined
): number {
  // created__date__gte and created__date__lte are one date range.
  return new Set(
    Object.keys(aiSearchScopeToParams(scope)).map((key) => key.split('__')[0])
  ).size
}

export function aiSearchScopeIsEmpty(
  scope: AiSearchScope | undefined
): boolean {
  return aiSearchScopeFilterCount(scope) === 0
}
