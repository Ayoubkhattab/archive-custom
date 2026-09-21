/**
 * Helpers for the streamed AI answer.
 *
 * The answer is streamed as plain text through whatever proxy sits in front of
 * the server, and that proxy can cut a slow response and put its own error page
 * in its place. These keep that from ever being shown as if it were an answer.
 */

/**
 * A zero-width space the server sends at once and then every few seconds while
 * the model is still thinking, so a proxy that gives up on a silent connection
 * (Cloudflare does after 120 seconds) never sees one.
 */
export const STREAM_HEARTBEAT = '​'

const HEARTBEATS = /​/g

/** The text with the keep-alive characters removed. */
export function stripHeartbeats(text: string): string {
  return text.replace(HEARTBEATS, '')
}

/**
 * Whether the received text is an error page written by a proxy rather than an
 * answer. Cloudflare's 5xx pages are JSON when the client accepts text.
 */
export function isProxyErrorBody(text: string): boolean {
  return /^\s*\{\s*"type"\s*:\s*"https:\/\/developers\.cloudflare\.com/.test(
    text
  )
}

/** What to tell the person when the request failed with this HTTP status. */
export function describeHttpFailure(status: number | undefined): string {
  switch (status) {
    case 408:
    case 504:
    case 524:
      return 'انتهت مهلة الاتصال قبل أن يرد الخادم. جرّب نمط الإجابة السريعة أو سؤالاً أقصر.'
    case 502:
    case 503:
    case 521:
    case 522:
    case 523:
      return 'الخادم غير متاح مؤقتاً، حاول بعد قليل.'
    case 429:
      return 'طلبات كثيرة في وقت قصير، انتظر قليلاً ثم أعد المحاولة.'
    case 401:
      return 'انتهت جلسة الدخول، أعد تسجيل الدخول.'
    case 403:
      return 'لا تملك صلاحية استعمال المحادثة مع المستندات.'
    default:
      return 'تعذّر الحصول على رد من نموذج الذكاء الاصطناعي، يرجى المحاولة مجدداً.'
  }
}
