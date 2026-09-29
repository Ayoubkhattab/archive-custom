import { normalizeForComparison } from './arabic-text'

/**
 * Parsing and checking the answers of "Ask about this file".
 *
 * The server asks the model to end each answer with a line
 * `المصدر: «verbatim quote»` (see paperless_ai/document_ask.py). Here that line
 * is split off and every quote is looked up in the document's own text, so
 * the reader can see at a glance whether the answer is grounded in the file.
 */

export enum QuoteStatus {
  /** Found in the document as written (ignoring spacing, marks, punctuation). */
  Exact = 'exact',
  /** Most of its words are in the document, not necessarily together. */
  Partial = 'partial',
  /** Not in the document: the model may have paraphrased or invented it. */
  Missing = 'missing',
}

export interface CheckedQuote {
  text: string
  status: QuoteStatus
}

export interface ParsedAnswer {
  /** The answer without its source line. */
  body: string
  quotes: string[]
  /** The model said the document doesn't contain the answer. */
  notInDocument: boolean
}

// The label may be bolded by the model, use either colon, and, although it is
// asked to put it on a line of its own, small models often append it to the
// last sentence ("… 391. المصدر: «…»"), so it is found anywhere after a space
// or punctuation. A colon is required, so the plain word "المصدر" in an
// answer is not mistaken for it.
const SOURCE_LABEL =
  /(^|[\s(.،؛:])[>*_-]*[ \t]*(?:المصدر|المصادر|المرجع|Sources?)[ \t*_]*[:：]/giu

// Without a label, only a quotation long enough to be evidence counts.
const MIN_UNLABELLED_QUOTE_WORDS = 4

const QUOTE = /«([^»]{2,}?)»|“([^”]{2,}?)”|"([^"\n]{2,}?)"/gu

const NOT_IN_DOCUMENT = /لم\s+يرد\s+ذلك\s+في\s+المستند|لا\s+يرد\s+في\s+المستند/u

// Quotes shorter than this carry too little to prove anything ("نعم").
const MIN_QUOTE_WORDS = 2
const PARTIAL_THRESHOLD = 0.8

export function parseAnswer(text: string): ParsedAnswer {
  const source = text ?? ''
  let sourceStart = -1
  for (const match of source.matchAll(SOURCE_LABEL)) {
    sourceStart = match.index + match[1].length
  }

  const body = (sourceStart >= 0 ? source.slice(0, sourceStart) : source)
    .trim()
    // What a mid-sentence label leaves behind ("…391. " or "…(").
    .replace(/[\s،؛:(]+$/u, '')
  const tail = sourceStart >= 0 ? source.slice(sourceStart) : ''

  const quotes: string[] = []
  for (const match of tail.matchAll(QUOTE)) {
    const quote = (match[1] ?? match[2] ?? match[3] ?? '').trim()
    if (quote && !quotes.includes(quote)) quotes.push(quote)
  }
  if (sourceStart < 0) {
    // No label: the model may still have quoted the document inline.
    for (const match of body.matchAll(QUOTE)) {
      const quote = (match[1] ?? match[2] ?? match[3] ?? '').trim()
      const words = quote.split(/\s+/).filter(Boolean).length
      if (words >= MIN_UNLABELLED_QUOTE_WORDS && !quotes.includes(quote)) {
        quotes.push(quote)
      }
    }
  }

  return {
    body,
    quotes,
    notInDocument: NOT_IN_DOCUMENT.test(body),
  }
}

export function checkQuote(quote: string, documentText: string): QuoteStatus {
  const needle = normalizeForComparison(quote)
  const haystack = normalizeForComparison(documentText)
  if (!needle || !haystack) return QuoteStatus.Missing
  if (` ${haystack} `.includes(` ${needle} `) || haystack.includes(needle)) {
    return QuoteStatus.Exact
  }

  const words = needle.split(' ').filter((w) => w.length > 1)
  if (words.length < MIN_QUOTE_WORDS) return QuoteStatus.Missing
  const vocabulary = new Set(haystack.split(' '))
  const found = words.filter((w) => vocabulary.has(w)).length
  return found / words.length >= PARTIAL_THRESHOLD
    ? QuoteStatus.Partial
    : QuoteStatus.Missing
}

export function checkQuotes(
  quotes: string[],
  documentText: string
): CheckedQuote[] {
  return quotes.map((text) => ({
    text,
    status: checkQuote(text, documentText),
  }))
}

/**
 * Where a quote sits in the raw document text, for selecting it in the content
 * editor. Tolerates any run of whitespace/punctuation between the quote's
 * words (OCR line breaks), but not changed words. Null if not locatable.
 */
export function locateQuote(
  quote: string,
  documentText: string
): { start: number; end: number } | null {
  if (!quote || !documentText) return null
  const direct = documentText.indexOf(quote)
  if (direct >= 0) return { start: direct, end: direct + quote.length }

  // Letters and digits only, so nothing in a word needs escaping.
  const words = quote.split(/[^\p{L}\p{N}]+/u).filter(Boolean)
  if (!words.length) return null
  // Arabic marks and tatweel may sit between any two letters in the source.
  const marks = '[\\u064B-\\u065F\\u0670\\u0640]*'
  const pattern = words
    .map((w) => Array.from(w).join(marks))
    .join('[^\\p{L}\\p{N}]+')
  try {
    const match = new RegExp(pattern, 'u').exec(documentText)
    return match ? { start: match.index, end: match.index + match[0].length } : null
  } catch {
    return null
  }
}
