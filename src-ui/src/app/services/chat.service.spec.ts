import {
  HttpEventType,
  provideHttpClient,
  withInterceptorsFromDi,
} from '@angular/common/http'
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing'
import { TestBed } from '@angular/core/testing'
import { environment } from 'src/environments/environment'
import { ChatService } from './chat.service'

describe('ChatService', () => {
  let service: ChatService
  let httpMock: HttpTestingController

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [],
      providers: [
        ChatService,
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
      ],
    })
    service = TestBed.inject(ChatService)
    httpMock = TestBed.inject(HttpTestingController)
  })

  afterEach(() => {
    httpMock.verify()
  })

  it('should stream chat messages', (done) => {
    const documentId = 1
    const prompt = 'Hello, world!'
    const mockResponse = 'Partial response text'
    const apiUrl = `${environment.apiBaseUrl}documents/chat/`

    service.streamChat(documentId, prompt).subscribe((chunk) => {
      expect(chunk).toBe(mockResponse)
      done()
    })

    const req = httpMock.expectOne(apiUrl)
    expect(req.request.method).toBe('POST')
    expect(req.request.body).toEqual({
      document_id: documentId,
      q: prompt,
    })

    req.event({
      type: HttpEventType.DownloadProgress,
      partialText: mockResponse,
    } as any)
  })

  it('should send the answer mode only when one is given', () => {
    const apiUrl = `${environment.apiBaseUrl}documents/chat/`

    service.streamChat(undefined, 'q', 'deep').subscribe()
    expect(httpMock.expectOne(apiUrl).request.body).toEqual({
      document_id: undefined,
      q: 'q',
      mode: 'deep',
    })

    service.streamChat(undefined, 'q').subscribe()
    expect(httpMock.expectOne(apiUrl).request.body).not.toHaveProperty('mode')
  })
})
