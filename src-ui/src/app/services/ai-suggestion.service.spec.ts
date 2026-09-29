import { provideHttpClient } from '@angular/common/http'
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing'
import { TestBed } from '@angular/core/testing'
import { environment } from 'src/environments/environment'
import { AiSuggestionService } from './ai-suggestion.service'

describe('AiSuggestionService', () => {
  let service: AiSuggestionService
  let http: HttpTestingController
  const url = `${environment.apiBaseUrl}documents/ai_suggest/classify/`

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    })
    service = TestBed.inject(AiSuggestionService)
    http = TestBed.inject(HttpTestingController)
  })

  afterEach(() => http.verify())

  it('strips heartbeats and parses the result', () => {
    let result
    service.classify(3).subscribe((r) => (result = r))
    const req = http.expectOne(url)
    expect(req.request.body).toEqual({ document_id: 3, apply: false })
    req.flush(
      '​​{"success": true, "classification": {"tags": ["فواتير"]}, "cached": true}'
    )
    expect(result).toEqual({
      classification: { tags: ['فواتير'] },
      cached: true,
      applied: false,
    })
  })

  it('reports a model failure sent inside the stream', () => {
    let message: string
    service.classify(3).subscribe({ error: (e) => (message = e.message) })
    http
      .expectOne(url)
      .flush('​{"success": false, "error": "تعذّر الاتصال بخادم النموذج"}')
    expect(message).toEqual('تعذّر الاتصال بخادم النموذج')
  })

  it('words a proxy timeout instead of showing the raw HTTP error', () => {
    let message: string
    service.classify(3).subscribe({ error: (e) => (message = e.message) })
    http
      .expectOne(url)
      .flush('', { status: 524, statusText: 'A timeout occurred' })
    expect(message).toContain('انتهت مهلة الاتصال')
    expect(message).not.toContain('Http failure')
  })

  it('treats an empty body as a failure', () => {
    let message: string
    service.classify(3, true).subscribe({ error: (e) => (message = e.message) })
    http.expectOne(url).flush('​​')
    expect(message).toBeTruthy()
  })
})
