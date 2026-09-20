import { directionForLanguage, isRtlLanguage } from './direction'

describe('Direction Utils', () => {
  it('should detect right-to-left languages', () => {
    expect(isRtlLanguage('ar')).toBeTruthy()
    expect(isRtlLanguage('he')).toBeTruthy()
    expect(isRtlLanguage('fa')).toBeTruthy()
    expect(isRtlLanguage('ur')).toBeTruthy()
  })

  it('should detect left-to-right languages', () => {
    expect(isRtlLanguage('en')).toBeFalsy()
    expect(isRtlLanguage('de')).toBeFalsy()
    expect(isRtlLanguage('zh')).toBeFalsy()
  })

  it('should match on the primary subtag regardless of shape or case', () => {
    // Angular locale ids, Django language codes and underscore variants all
    // reach this helper.
    expect(isRtlLanguage('ar-AR')).toBeTruthy()
    expect(isRtlLanguage('ar-ar')).toBeTruthy()
    expect(isRtlLanguage('ar_SA')).toBeTruthy()
    expect(isRtlLanguage('fa-IR')).toBeTruthy()
    expect(isRtlLanguage('en-US')).toBeFalsy()
  })

  it('should treat an unknown or missing language as left-to-right', () => {
    expect(isRtlLanguage('xx-XX')).toBeFalsy()
    expect(isRtlLanguage('')).toBeFalsy()
    expect(isRtlLanguage(null)).toBeFalsy()
    expect(isRtlLanguage(undefined)).toBeFalsy()
  })

  it('should map a language to its writing direction', () => {
    expect(directionForLanguage('ar-AR')).toEqual('rtl')
    expect(directionForLanguage('en-US')).toEqual('ltr')
    expect(directionForLanguage(null)).toEqual('ltr')
  })
})
