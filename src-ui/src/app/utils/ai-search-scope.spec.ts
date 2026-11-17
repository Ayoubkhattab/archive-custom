import {
  FILTER_CREATED_FROM,
  FILTER_CREATED_TO,
  FILTER_FULLTEXT_QUERY,
  FILTER_HAS_STORAGE_PATH_ANY,
  FILTER_HAS_TAGS_ALL,
} from 'src/app/data/filter-rule-type'
import {
  aiSearchScopeFilterCount,
  aiSearchScopeIsEmpty,
  aiSearchScopeToParams,
  cloneAiSearchScope,
  emptyAiSearchScope,
} from './ai-search-scope'

describe('ai-search-scope', () => {
  it('should be empty by default', () => {
    expect(aiSearchScopeToParams(emptyAiSearchScope())).toEqual({})
    expect(aiSearchScopeToParams(undefined)).toEqual({})
    expect(aiSearchScopeIsEmpty(emptyAiSearchScope())).toBeTruthy()
  })

  it('should turn rules and correspondence fields into list parameters', () => {
    const scope = {
      filterRules: [
        { rule_type: FILTER_HAS_TAGS_ALL, value: '1' },
        { rule_type: FILTER_HAS_TAGS_ALL, value: '2' },
        { rule_type: FILTER_HAS_STORAGE_PATH_ANY, value: '5' },
        { rule_type: FILTER_CREATED_FROM, value: '2024-01-01' },
        { rule_type: FILTER_CREATED_TO, value: '2024-12-31' },
        { rule_type: FILTER_FULLTEXT_QUERY, value: 'created:[-1 month to now]' },
      ],
      senders: [3, 4],
      recipients: [8],
      classifications: [2],
    }
    expect(aiSearchScopeToParams(scope)).toEqual({
      tags__id__all: '1,2',
      storage_path__id__in: '5',
      created__date__gte: '2024-01-01',
      created__date__lte: '2024-12-31',
      query: 'created:[-1 month to now]',
      sender__id__in: '3,4',
      recipient__id__in: '8',
      classification__id__in: '2',
    })
    // A date range is one filter, however many bounds it has.
    expect(aiSearchScopeFilterCount(scope)).toEqual(7)
  })

  it('should copy deeply and drop whatever storage corrupted', () => {
    const original = {
      ...emptyAiSearchScope(),
      filterRules: [{ rule_type: FILTER_HAS_TAGS_ALL, value: '1' }],
      senders: [1],
    }
    const copy = cloneAiSearchScope(original)
    copy.filterRules[0].value = '9'
    copy.senders.push(2)
    expect(original.filterRules[0].value).toEqual('1')
    expect(original.senders).toEqual([1])

    expect(
      cloneAiSearchScope({
        filterRules: [{ rule_type: 99999, value: 'x' }, null],
        senders: 'nope',
        recipients: [1, 'two', 3.5],
      } as any)
    ).toEqual({
      filterRules: [],
      senders: [],
      recipients: [1],
      classifications: [],
    })
    expect(cloneAiSearchScope('junk' as any)).toEqual(emptyAiSearchScope())
  })
})
