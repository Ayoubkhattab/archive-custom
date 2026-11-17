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
import { UploadDocumentsService } from './upload-documents.service'
import {
  FileStatusPhase,
  WebsocketStatusService,
} from './websocket-status.service'

describe('UploadDocumentsService', () => {
  let httpTestingController: HttpTestingController
  let uploadDocumentsService: UploadDocumentsService
  let websocketStatusService: WebsocketStatusService

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [],
      providers: [
        UploadDocumentsService,
        WebsocketStatusService,
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
      ],
    })

    httpTestingController = TestBed.inject(HttpTestingController)
    uploadDocumentsService = TestBed.inject(UploadDocumentsService)
    websocketStatusService = TestBed.inject(WebsocketStatusService)
  })

  afterEach(() => {
    httpTestingController.verify()
  })

  it('calls post_document api endpoint on upload', () => {
    const file = new File(
      [new Blob(['testing'], { type: 'application/pdf' })],
      'file.pdf'
    )
    uploadDocumentsService.uploadFile(file)
    const req = httpTestingController.match(
      `${environment.apiBaseUrl}documents/post_document/`
    )
    expect(req[0].request.method).toEqual('POST')

    req[0].flush('123-456')
  })

  it('updates progress during upload and failure', () => {
    const file = new File(
      [new Blob(['testing'], { type: 'application/pdf' })],
      'file.pdf'
    )
    const file2 = new File(
      [new Blob(['testing'], { type: 'application/pdf' })],
      'file2.pdf'
    )
    uploadDocumentsService.uploadFile(file)
    uploadDocumentsService.uploadFile(file2)

    expect(websocketStatusService.getConsumerStatusNotCompleted()).toHaveLength(
      2
    )
    expect(
      websocketStatusService.getConsumerStatus(FileStatusPhase.UPLOADING)
    ).toHaveLength(0)

    const req = httpTestingController.match(
      `${environment.apiBaseUrl}documents/post_document/`
    )

    req[0].event({
      type: HttpEventType.UploadProgress,
      loaded: 100,
      total: 300,
    })

    expect(
      websocketStatusService.getConsumerStatus(FileStatusPhase.UPLOADING)
    ).toHaveLength(1)
  })

  it('updates progress on failure', () => {
    const file = new File(
      [new Blob(['testing'], { type: 'application/pdf' })],
      'file.pdf'
    )
    uploadDocumentsService.uploadFile(file)

    let req = httpTestingController.match(
      `${environment.apiBaseUrl}documents/post_document/`
    )

    expect(
      websocketStatusService.getConsumerStatus(FileStatusPhase.FAILED)
    ).toHaveLength(0)

    req[0].flush(
      {},
      {
        status: 400,
        statusText: 'failed',
      }
    )

    expect(
      websocketStatusService.getConsumerStatus(FileStatusPhase.FAILED)
    ).toHaveLength(1)

    uploadDocumentsService.uploadFile(file)

    req = httpTestingController.match(
      `${environment.apiBaseUrl}documents/post_document/`
    )

    req[0].flush(
      {},
      {
        status: 500,
        statusText: 'failed',
      }
    )

    expect(
      websocketStatusService.getConsumerStatus(FileStatusPhase.FAILED)
    ).toHaveLength(2)
  })

  describe('chunked uploads', () => {
    const sessionId = 'a'.repeat(32)
    const sessionsUrl = `${environment.apiBaseUrl}documents/upload_sessions/`
    const chunkUrl = (i: number) => `${sessionsUrl}${sessionId}/chunks/${i}/`
    const completeUrl = `${sessionsUrl}${sessionId}/complete/`
    // Lets the awaits inside the service run.
    const settle = () => new Promise((resolve) => setTimeout(resolve, 0))
    const bigFile = () => new File(['0123456789'], 'big.pdf')
    const session = (received: number[] = []) => ({
      id: sessionId,
      chunk_size: 4,
      total_chunks: 3,
      received,
      task_id: null,
    })

    beforeEach(() => {
      uploadDocumentsService.chunkedThreshold = 4
      uploadDocumentsService.retryBaseDelayMs = 0
    })

    it('sends chunk 0 first, then the rest, then completes', async () => {
      uploadDocumentsService.uploadFile(bigFile())

      const create = httpTestingController.expectOne(sessionsUrl)
      expect(create.request.method).toEqual('POST')
      expect(create.request.body).toMatchObject({
        filename: 'big.pdf',
        size: 10,
      })
      create.flush(session())
      await settle()

      const first = httpTestingController.expectOne(chunkUrl(0))
      expect(first.request.method).toEqual('PUT')
      expect((first.request.body as Blob).size).toEqual(4)
      // Nothing else is sent until the server accepted the file type.
      httpTestingController.expectNone(chunkUrl(1))
      first.flush({ index: 0 })
      await settle()

      const last = httpTestingController.expectOne(chunkUrl(2))
      expect((last.request.body as Blob).size).toEqual(2)
      httpTestingController.expectOne(chunkUrl(1)).flush({ index: 1 })
      last.flush({ index: 2 })
      await settle()

      const complete = httpTestingController.expectOne(completeUrl)
      expect(complete.request.body).toEqual({ from_webui: true })
      complete.flush('task-1')
      await settle()

      const [status] = websocketStatusService.getConsumerStatus()
      expect(status.taskId).toEqual('task-1')
      expect(status.phase).toEqual(FileStatusPhase.UPLOADING)
    })

    it('resumes by sending only the chunks the server is missing', async () => {
      uploadDocumentsService.uploadFile(bigFile())

      httpTestingController.expectOne(sessionsUrl).flush(session([0, 1]))
      await settle()

      httpTestingController.expectNone(chunkUrl(0))
      httpTestingController.expectNone(chunkUrl(1))
      httpTestingController.expectOne(chunkUrl(2)).flush({ index: 2 })
      await settle()

      httpTestingController.expectOne(completeUrl).flush('task-2')
      await settle()
      expect(websocketStatusService.getConsumerStatus()[0].taskId).toEqual(
        'task-2'
      )
    })

    it('retries a chunk after a network error', async () => {
      uploadDocumentsService.uploadFile(bigFile())
      httpTestingController.expectOne(sessionsUrl).flush(session([1, 2]))
      await settle()

      httpTestingController
        .expectOne(chunkUrl(0))
        .error(new ProgressEvent('error'))
      await settle()
      await settle()

      httpTestingController.expectOne(chunkUrl(0)).flush({ index: 0 })
      await settle()
      httpTestingController.expectOne(completeUrl).flush('task-3')
      await settle()
      expect(websocketStatusService.getConsumerStatus()[0].taskId).toEqual(
        'task-3'
      )
    })

    it('reports an interrupted upload after the retries run out', async () => {
      uploadDocumentsService.maxRetries = 1
      uploadDocumentsService.uploadFile(bigFile())
      httpTestingController.expectOne(sessionsUrl).flush(session([1, 2]))
      await settle()

      for (let attempt = 0; attempt < 2; attempt++) {
        httpTestingController
          .expectOne(chunkUrl(0))
          .flush(null, { status: 502, statusText: 'Bad Gateway' })
        await settle()
        await settle()
      }

      const [status] = websocketStatusService.getConsumerStatus(
        FileStatusPhase.FAILED
      )
      // Chunks 1 and 2 (6 of 10 bytes) were already on the server.
      expect(status.message).toContain('60%')
    })

    it('fails at once when the server refuses the file type', async () => {
      uploadDocumentsService.uploadFile(bigFile())
      httpTestingController.expectOne(sessionsUrl).flush(session())
      await settle()

      httpTestingController
        .expectOne(chunkUrl(0))
        .flush(
          { document: ['File type text/plain not supported'] },
          { status: 400, statusText: 'Bad Request' }
        )
      await settle()

      const [status] = websocketStatusService.getConsumerStatus(
        FileStatusPhase.FAILED
      )
      expect(status.message).toEqual(['File type text/plain not supported'])
      httpTestingController.expectNone(chunkUrl(1))
    })
  })
})
