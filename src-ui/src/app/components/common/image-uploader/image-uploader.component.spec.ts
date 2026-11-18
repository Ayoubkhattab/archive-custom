import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http'
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing'
import { ComponentFixture, TestBed } from '@angular/core/testing'
import { NgxBootstrapIconsModule, allIcons } from 'ngx-bootstrap-icons'
import { User } from 'src/app/data/user'
import { PermissionsService } from 'src/app/services/permissions.service'
import { SettingsService } from 'src/app/services/settings.service'
import {
  ImageUploaderComponent,
  uploadErrorMessage,
} from './image-uploader.component'
import { HttpErrorResponse } from '@angular/common/http'

const ENDPOINT = '/api/images/'

const father: User = { id: 1, username: 'father' }
const son: User = {
  id: 2,
  username: 'son',
  parent: 1,
  ancestors: [1],
  access: ['image.upload.self', 'image.delete.self', 'image.upload.any'],
}

function file(name = 'a.jpg', type = 'image/jpeg', size = 1000): File {
  const f = new File([new Uint8Array(10)], name, { type })
  Object.defineProperty(f, 'size', { value: size })
  return f
}

describe('ImageUploaderComponent', () => {
  let fixture: ComponentFixture<ImageUploaderComponent>
  let component: ImageUploaderComponent
  let http: HttpTestingController
  let permissions: PermissionsService

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [ImageUploaderComponent, NgxBootstrapIconsModule.pick(allIcons)],
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
      ],
    })
    permissions = TestBed.inject(PermissionsService)
    permissions.initialize([], son)
    TestBed.inject(SettingsService).currentUser = son
    fixture = TestBed.createComponent(ImageUploaderComponent)
    component = fixture.componentInstance
    http = TestBed.inject(HttpTestingController)
    fixture.detectChanges()
  })

  afterEach(() => http.verify())

  function el(selector: string): HTMLElement {
    fixture.detectChanges()
    return fixture.nativeElement.querySelector(selector)
  }

  it('shows the drop zone for own uploads', () => {
    expect(el('.drop-zone')).not.toBeNull()
  })

  it('hides the upload control when the target is an ancestor', () => {
    // son has image.upload.any, but never over his father
    fixture.componentRef.setInput('owner', father)
    expect(component.canUpload).toBe(false)
    expect(el('.drop-zone')).toBeNull()
  })

  it('hides the upload control without permission', () => {
    permissions.initialize([], { ...son, access: [] })
    TestBed.inject(SettingsService).currentUser = { ...son, access: [] }
    fixture = TestBed.createComponent(ImageUploaderComponent)
    expect(el('.drop-zone')).toBeNull()
  })

  it('reports progress and completion', () => {
    const uploaded = jest.fn()
    component.uploaded.subscribe(uploaded)
    component.addFiles([file()])
    const req = http.expectOne(ENDPOINT)
    expect(req.request.body.get('idempotency_key')).toMatch(/^img-/)
    req.event({ type: 1, loaded: 50, total: 100 } as any) // UploadProgress
    expect(component.items[0].progress).toBe(50)
    expect(el('.progress-bar').textContent).toContain('50%')
    req.flush({ id: 'x', can_delete: true })
    expect(component.items[0].status).toBe('done')
    expect(uploaded).toHaveBeenCalledWith({ id: 'x', can_delete: true })
  })

  it('cancel aborts the request and retry reuses the idempotency key', () => {
    component.addFiles([file()])
    const first = http.expectOne(ENDPOINT)
    const key = first.request.body.get('idempotency_key')
    component.cancel(component.items[0])
    expect(first.cancelled).toBe(true)
    expect(component.items[0].status).toBe('cancelled')

    component.retry(component.items[0])
    const second = http.expectOne(ENDPOINT)
    expect(second.request.body.get('idempotency_key')).toBe(key)
    second.flush({ id: 'x', can_delete: true })
    expect(component.items[0].status).toBe('done')
  })

  it.each([
    [401, 'Your session has expired'],
    [403, 'You do not have permission to upload images here.'],
    [413, 'The file is too large.'],
    [415, 'This file type is not supported.'],
    [429, 'Too many uploads'],
    [500, 'The server failed to process the image'],
  ])('shows a clear message for HTTP %s', (status, message) => {
    component.addFiles([file()])
    http.expectOne(ENDPOINT).flush({ code: 'x', detail: 'x' }, { status, statusText: 'x' })
    expect(component.items[0].status).toBe('error')
    expect(el('.error').textContent).toContain(message)
    const retryShown = !!el('.retry')
    expect(retryShown).toBe(![403, 413, 415].includes(status))
  })

  it('shows the server detail for 422', () => {
    expect(
      uploadErrorMessage(
        new HttpErrorResponse({
          status: 422,
          error: { code: 'invalid_image', detail: 'The image is corrupted or unreadable.' },
        })
      )
    ).toBe('The image is corrupted or unreadable.')
  })

  it('pre-validates type and size without contacting the server', () => {
    component.addFiles([file('a.svg', 'image/svg+xml'), file('b.jpg', 'image/jpeg', 50 * 1024 * 1024)])
    expect(component.items.map((i) => i.status)).toEqual(['invalid', 'invalid'])
    http.expectNone(ENDPOINT)
  })

  it('remove deletes uploaded images and revokes previews', () => {
    const revoke = URL.revokeObjectURL as jest.Mock
    ;(URL.createObjectURL as jest.Mock).mockReturnValue('blob:preview')
    revoke.mockClear()
    component.addFiles([file()])
    http.expectOne(ENDPOINT).flush({ id: 'abc', can_delete: true })
    component.remove(component.items[0])
    http.expectOne({ method: 'DELETE', url: `${ENDPOINT}abc/` }).flush(null)
    expect(component.items.length).toBe(0)
    expect(revoke).toHaveBeenCalledWith('blob:preview')
  })

  it('hides remove for uploads the server says cannot be deleted', () => {
    component.addFiles([file()])
    http.expectOne(ENDPOINT).flush({ id: 'abc', can_delete: false })
    expect(el('.remove')).toBeNull()
  })
})
