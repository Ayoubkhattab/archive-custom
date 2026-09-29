/**
 * Normalises text for matching so that Arabic spelling variants, diacritics
 * and digit styles don't hide a match: all alef forms → ا, ى → ي, ة → ه,
 * tashkeel / tatweel removed, Arabic-Indic digits → ASCII. Latin text is
 * lower-cased and stripped of accents.
 */
export function normalizeForSearch(value: string): string {
  return (value ?? '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[ً-ٰٟـ]/g, '') // tashkeel, superscript alef, tatweel
    .replace(/[آأإٱ]/g, 'ا') // آ أ إ ٱ → ا
    .replace(/ى/g, 'ي') // ى → ي
    .replace(/ة/g, 'ه') // ة → ه
    .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
    .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06f0))
    .replace(/[̀-ͯ]/g, '') // Latin combining marks
    .trim()
}

/**
 * Like `normalizeForSearch`, and also reduces all punctuation and whitespace
 * runs to a single space, so OCR line breaks and a model's changed punctuation
 * don't stop a quote from being found.
 */
export function normalizeForComparison(value: string): string {
  return normalizeForSearch(value)
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
}

/** Every whitespace-separated term of the query must appear somewhere. */
export function matchesQuery(haystack: string, query: string): boolean {
  const terms = normalizeForSearch(query).split(/\s+/).filter(Boolean)
  if (!terms.length) return true
  const text = normalizeForSearch(haystack)
  return terms.every((t) => text.includes(t))
}
