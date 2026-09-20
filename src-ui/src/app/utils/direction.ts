/**
 * Text direction helpers.
 *
 * The frontend is built once per locale (see the `i18n` block in angular.json)
 * and Django picks the matching bundle, so the document direction is normally
 * decided server-side and rendered into the `dir` attribute of `<html>`.
 * These helpers are the single source of truth for that decision so the server
 * template, the runtime language switch and `ng serve` all agree.
 */

export type TextDirection = 'ltr' | 'rtl'

/**
 * Language subtags written right-to-left. Matched on the primary subtag only,
 * so any region/script variant ('ar-AR', 'fa-IR', 'ar_SA', …) resolves too.
 */
const RTL_LANGUAGES = new Set([
  'ar', // Arabic
  'arc', // Aramaic
  'ckb', // Central Kurdish (Sorani)
  'dv', // Divehi
  'fa', // Persian
  'ha', // Hausa (Arabic script)
  'he', // Hebrew
  'iw', // Hebrew (legacy subtag)
  'khw', // Khowar
  'ks', // Kashmiri
  'ku', // Kurdish
  'ps', // Pashto
  'sd', // Sindhi
  'syr', // Syriac
  'ug', // Uyghur
  'ur', // Urdu
  'yi', // Yiddish
])

/**
 * Whether a BCP 47-ish language tag is written right-to-left.
 *
 * Accepts the shapes this app passes around interchangeably: Angular locale ids
 * ('ar-AR'), Django language codes ('ar-ar'), underscore variants ('ar_AR') and
 * bare primary subtags ('ar').
 */
export function isRtlLanguage(language: string | null | undefined): boolean {
  if (!language) return false
  const primarySubtag = language.toLowerCase().split(/[-_]/)[0]
  return RTL_LANGUAGES.has(primarySubtag)
}

/** The writing direction for a language tag, defaulting to LTR when unknown. */
export function directionForLanguage(
  language: string | null | undefined
): TextDirection {
  return isRtlLanguage(language) ? 'rtl' : 'ltr'
}
