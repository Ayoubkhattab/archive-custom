import {
  Component,
  EventEmitter,
  Input,
  Output,
  SimpleChange,
} from '@angular/core'
import {
  ComponentFixture,
  discardPeriodicTasks,
  fakeAsync,
  TestBed,
  tick,
} from '@angular/core/testing'
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http'
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing'
import { NgxBootstrapIconsModule, allIcons } from 'ngx-bootstrap-icons'
import { PdfViewerModule } from 'ng2-pdf-viewer'
import {
  looksLikePdf,
  PDF_ERROR_MESSAGES,
  PdfViewerComponent,
  PdfViewerState,
} from './pdf-viewer.component'

/** Stand-in for ng2-pdf-viewer so jsdom never loads PDF.js. */
@Component({ selector: 'pdf-viewer', template: '' })
class StubPdfViewerComponent {
  @Input() src: any
  @Input('original-size') originalSize: boolean
  @Input('show-borders') showBorders: boolean
  @Input('show-all') showAll: boolean
  @Input('render-text') renderText: boolean
  @Input() page: number
  @Input() zoom: number
  @Input('zoom-scale') zoomScale: string
  @Output() pageChange = new EventEmitter<number>()
  @Output('after-load-complete') afterLoadComplete = new EventEmitter()
  @Output('error') onError = new EventEmitter()
}

const URL_ = '/api/documents/1/preview/'

/** Bytes built in the test realm (jsdom's TextEncoder uses another realm). */
function bytes(text: string): ArrayBuffer {
  return new Uint8Array(Array.from(text, (c) => c.charCodeAt(0) & 0xff)).buffer
}

function pdfBytes(): ArrayBuffer {
  return bytes('%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n')
}

describe('PdfViewerComponent', () => {
  let fixture: ComponentFixture<PdfViewerComponent>
  let component: PdfViewerComponent
  let http: HttpTestingController
  let states: PdfViewerState[]

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [PdfViewerComponent, NgxBootstrapIconsModule.pick(allIcons)],
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
      ],
    })
    TestBed.overrideComponent(PdfViewerComponent, {
      remove: { imports: [PdfViewerModule] },
      add: { imports: [StubPdfViewerComponent] },
    })
    fixture = TestBed.createComponent(PdfViewerComponent)
    component = fixture.componentInstance
    http = TestBed.inject(HttpTestingController)
    states = []
    component.stateChange.subscribe((s) => states.push(s))
  })

  function start(src = URL_) {
    component.src = src
    component.ngOnChanges({ src: new SimpleChange(null, src, true) })
    fixture.detectChanges()
  }

  function text(): string {
    fixture.detectChanges()
    return fixture.nativeElement.textContent
  }

  function fakePdf(numPages = 3) {
    return { numPages, destroy: jest.fn() } as any
  }

  it('goes loading -> rendering -> success', () => {
    const loaded = jest.fn()
    component.loaded.subscribe(loaded)
    start()
    expect(component.state).toBe('loading')
    expect(text()).toContain('Loading...')
    http.expectOne(URL_).flush(pdfBytes())
    expect(component.state).toBe('rendering')
    expect(component.pdfSrc.data).toBeInstanceOf(Uint8Array)

    const pdf = fakePdf()
    component.onLoadComplete(pdf)
    expect(component.state).toBe('success')
    expect(loaded).toHaveBeenCalledWith(pdf)
    expect(states).toEqual(['loading', 'rendering', 'success'])
    expect(text()).not.toContain('Loading...')
  })

  it('retries a 401 once, then succeeds', fakeAsync(() => {
    start()
    http
      .expectOne(URL_)
      .flush(new ArrayBuffer(0), { status: 401, statusText: 'Unauthorized' })
    tick(0)
    http.expectOne(URL_).flush(pdfBytes())
    expect(component.state).toBe('rendering')
    discardPeriodicTasks()
    component.ngOnDestroy()
  }))

  it('shows a session message after a second 401', fakeAsync(() => {
    start()
    http
      .expectOne(URL_)
      .flush(new ArrayBuffer(0), { status: 401, statusText: 'Unauthorized' })
    tick(0)
    http
      .expectOne(URL_)
      .flush(new ArrayBuffer(0), { status: 401, statusText: 'Unauthorized' })
    expect(component.state).toBe('error')
    expect(component.errorKind).toBe('unauthorized')
    expect(text()).toContain('Sign in')
  }))

  it.each([
    [403, 'forbidden', 'You do not have permission to view this PDF.'],
    [404, 'not_found', 'This PDF could not be found.'],
    [416, 'range', PDF_ERROR_MESSAGES.range],
    [500, 'server', PDF_ERROR_MESSAGES.server],
    [503, 'server', PDF_ERROR_MESSAGES.server],
  ])('maps HTTP %s to a clear error', (status, kind, message) => {
    start()
    http
      .expectOne(URL_)
      .flush(new ArrayBuffer(0), { status, statusText: 'x' })
    expect(component.state).toBe('error')
    expect(component.errorKind).toBe(kind)
    expect(text()).toContain(message)
    // no retry offered when access itself is the problem
    const retry = fixture.nativeElement.querySelector('button')
    if (status === 403 || status === 404) expect(retry).toBeNull()
    else expect(retry).not.toBeNull()
  })

  it('never stays on "Loading" when the server does not answer', fakeAsync(() => {
    start()
    const req = http.expectOne(URL_)
    tick(19999)
    expect(component.state).toBe('loading')
    tick(1)
    expect(component.state).toBe('error')
    expect(component.errorKind).toBe('timeout')
    expect(req.cancelled).toBe(true) // aborted
    expect(text()).not.toContain('Loading...')
  }))

  it('times out a render that never completes', fakeAsync(() => {
    start()
    http.expectOne(URL_).flush(pdfBytes())
    expect(component.state).toBe('rendering')
    tick(20000)
    expect(component.state).toBe('error')
    expect(component.errorKind).toBe('timeout')
  }))

  it('detects empty and corrupted files before rendering', () => {
    start()
    http.expectOne(URL_).flush(new ArrayBuffer(0))
    expect(component.errorKind).toBe('empty')

    start()
    http.expectOne(URL_).flush(bytes('<html>'))
    expect(component.errorKind).toBe('corrupted')

    start()
    http.expectOne(URL_).flush(pdfBytes())
    component.onRenderError({ name: 'InvalidPDFException' })
    expect(component.errorKind).toBe('corrupted')

    start()
    http.expectOne(URL_).flush(pdfBytes())
    component.onLoadComplete(fakePdf(0))
    expect(component.errorKind).toBe('empty')
  })

  it('asks for a password for encrypted PDFs and re-renders with it', () => {
    const asked = jest.fn()
    component.passwordRequired.subscribe(asked)
    start()
    http.expectOne(URL_).flush(pdfBytes())
    component.onRenderError({ name: 'PasswordException', code: 1 })
    expect(component.state).toBe('password')
    expect(component.errorKind).toBe('encrypted')
    expect(asked).toHaveBeenCalled()

    component.password = 'secret'
    component.ngOnChanges({ password: new SimpleChange(null, 'secret', false) })
    expect(component.state).toBe('rendering')
    expect(component.pdfSrc.password).toBe('secret')
    http.expectNone(URL_) // no second download

    component.onRenderError({ name: 'PasswordException', code: 2 })
    expect(component.errorKind).toBe('wrong_password')
  })

  it('retry downloads again', () => {
    start()
    http.expectOne(URL_).flush(new ArrayBuffer(0), { status: 500, statusText: 'x' })
    component.retry()
    http.expectOne(URL_).flush(pdfBytes())
    expect(component.state).toBe('rendering')
  })

  it('cleans up on destroy: aborts, destroys the document, revokes blob URLs', () => {
    // setup-jest.ts installs jest.fn() for both
    const create = URL.createObjectURL as jest.Mock
    const revoke = URL.revokeObjectURL as jest.Mock
    create.mockReturnValue('blob:pdf')
    revoke.mockClear()

    start()
    http.expectOne(URL_).flush(pdfBytes())
    const pdf = fakePdf()
    component.onLoadComplete(pdf)
    expect(component.fallbackUrl).toBe('blob:pdf')
    component.ngOnDestroy()
    expect(pdf.destroy).toHaveBeenCalled()
    expect(revoke).toHaveBeenCalledWith('blob:pdf')

    start()
    const req = http.expectOne(URL_)
    component.ngOnDestroy()
    expect(req.cancelled).toBe(true)
  })

  it('prefers the given download URL for the fallback', () => {
    component.downloadUrl = '/api/documents/1/download/'
    expect(component.fallbackUrl).toBe('/api/documents/1/download/')
  })

  it('looksLikePdf', () => {
    expect(looksLikePdf(pdfBytes())).toBe(true)
    expect(looksLikePdf(bytes('GIF89a'))).toBe(false)
  })

  afterEach(() => {
    http.verify()
  })
})
