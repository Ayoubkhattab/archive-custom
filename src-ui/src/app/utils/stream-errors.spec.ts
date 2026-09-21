import {
  describeHttpFailure,
  isProxyErrorBody,
  STREAM_HEARTBEAT,
  stripHeartbeats,
} from './stream-errors'

describe('Stream error helpers', () => {
  it('should remove the keep-alive characters and nothing else', () => {
    expect(stripHeartbeats(`${STREAM_HEARTBEAT}${STREAM_HEARTBEAT}`)).toEqual(
      ''
    )
    expect(
      stripHeartbeats(`جو${STREAM_HEARTBEAT}اب${STREAM_HEARTBEAT}`)
    ).toEqual('جواب')
    expect(stripHeartbeats('no heartbeat')).toEqual('no heartbeat')
  })

  it('should recognise a Cloudflare error page delivered as the body', () => {
    const body =
      '{"type":"https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/","title":"Error 524: A timeout occurred","status":524}'
    expect(isProxyErrorBody(body)).toBeTruthy()
    expect(isProxyErrorBody(`\n  ${body}`)).toBeTruthy()
  })

  it('should not mistake an ordinary answer for a proxy error', () => {
    expect(isProxyErrorBody('مرحباً، هذه إجابة عادية.')).toBeFalsy()
    expect(isProxyErrorBody('{"a": 1}')).toBeFalsy()
    expect(isProxyErrorBody('')).toBeFalsy()
    // Mentioning Cloudflare inside an answer is not an error page.
    expect(
      isProxyErrorBody('شرح عن https://developers.cloudflare.com/')
    ).toBeFalsy()
  })

  it('should explain timeouts, unavailability, rate limits and permissions', () => {
    for (const status of [408, 504, 524]) {
      expect(describeHttpFailure(status)).toContain('مهلة')
    }
    for (const status of [502, 503, 521, 522, 523]) {
      expect(describeHttpFailure(status)).toContain('غير متاح')
    }
    expect(describeHttpFailure(429)).toContain('كثيرة')
    expect(describeHttpFailure(401)).toContain('تسجيل الدخول')
    expect(describeHttpFailure(403)).toContain('صلاحية')
  })

  it('should fall back to a generic message for anything else', () => {
    expect(describeHttpFailure(500)).toContain('تعذّر الحصول على رد')
    expect(describeHttpFailure(undefined)).toContain('تعذّر الحصول على رد')
  })
})
