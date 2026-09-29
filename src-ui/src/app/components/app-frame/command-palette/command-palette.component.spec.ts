import { matchesQuery, normalizeForSearch } from 'src/app/utils/arabic-text'

describe('command palette matching', () => {
  it('ignores Arabic spelling variants and diacritics', () => {
    expect(normalizeForSearch('أرشيف')).toEqual(normalizeForSearch('ارشيف'))
    expect(normalizeForSearch('إدارة')).toEqual(normalizeForSearch('اداره'))
    expect(normalizeForSearch('مُسْتَنَد')).toEqual('مستند')
    expect(normalizeForSearch('مستشفى')).toEqual(normalizeForSearch('مستشفي'))
  })

  it('is case-insensitive for Latin text', () => {
    expect(matchesQuery('Saved Views', 'saved')).toBe(true)
  })

  it('requires every term to match', () => {
    expect(matchesQuery('عقد إيجار المبنى', 'عقد المبني')).toBe(true)
    expect(matchesQuery('عقد إيجار المبنى', 'عقد سيارة')).toBe(false)
  })

  it('matches everything for an empty query', () => {
    expect(matchesQuery('anything', '  ')).toBe(true)
  })
})
