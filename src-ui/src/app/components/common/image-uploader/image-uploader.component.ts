import {
  HttpErrorResponse,
  HttpEventType,
} from '@angular/common/http'
import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  EventEmitter,
  inject,
  Input,
  OnDestroy,
  Output,
} from '@angular/core'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { Subscription, TimeoutError } from 'rxjs'
import {
  IMAGE_UPLOAD_DEFAULTS,
  UploadedImage,
} from 'src/app/data/uploaded-image'
import { User } from 'src/app/data/user'
import { PermissionsService } from 'src/app/services/permissions.service'
import { ImageService } from 'src/app/services/rest/image.service'
import { SettingsService } from 'src/app/services/settings.service'

export type UploadStatus =
  | 'invalid'
  | 'uploading'
  | 'done'
  | 'error'
  | 'cancelled'

export interface UploadItem {
  localId: number
  file: File
  previewUrl: string
  status: UploadStatus
  progress: number
  error?: string
  httpStatus?: number
  result?: UploadedImage
  /** Reused on retry so the server never stores the same upload twice. */
  idempotencyKey: string
  subscription?: Subscription
}

export function uploadErrorMessage(error: unknown): string {
  if (error instanceof TimeoutError)
    return $localize`The upload timed out. Please try again.`
  if (!(error instanceof HttpErrorResponse))
    return $localize`The upload failed. Please try again.`
  switch (error.status) {
    case 0:
      return $localize`Network error. Check your connection and try again.`
    case 401:
      return $localize`Your session has expired. Please sign in again.`
    case 403:
      return $localize`You do not have permission to upload images here.`
    case 413:
      return $localize`The file is too large.`
    case 415:
      return $localize`This file type is not supported.`
    case 422:
      // server messages for 422 are written to be shown to users
      return error.error?.detail && error.error?.code !== 'validation_error'
        ? error.error.detail
        : $localize`The image is invalid.`
    case 429:
      return $localize`Too many uploads. Please wait a moment and try again.`
    default:
      return error.status >= 500
        ? $localize`The server failed to process the image. Please try again.`
        : $localize`The upload failed. Please try again.`
  }
}

let nextLocalId = 1

function newIdempotencyKey(): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '')
      : `${Date.now()}${Math.random().toString(36).slice(2)}`
  return `img-${random}`.slice(0, 64)
}

@Component({
  selector: 'pngx-image-uploader',
  templateUrl: './image-uploader.component.html',
  styleUrls: ['./image-uploader.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NgxBootstrapIconsModule],
})
export class ImageUploaderComponent implements OnDestroy {
  private imageService = inject(ImageService)
  private permissionsService = inject(PermissionsService)
  private settings = inject(SettingsService)
  private cdr = inject(ChangeDetectorRef)

  /** Account the images are uploaded for; defaults to the current user. */
  @Input() owner: User = null
  @Input() maxBytes = IMAGE_UPLOAD_DEFAULTS.maxBytes
  @Input() acceptedTypes = IMAGE_UPLOAD_DEFAULTS.acceptedTypes

  @Output() uploaded = new EventEmitter<UploadedImage>()
  @Output() removed = new EventEmitter<string>()

  items: UploadItem[] = []
  dragging = false

  get target(): User {
    return this.owner ?? this.settings.currentUser
  }

  /** Hide the control when the policy mirror says no (server re-checks). */
  get canUpload(): boolean {
    return this.permissionsService.canOnTarget('image.upload', this.target)
  }

  get accept(): string {
    return this.acceptedTypes.join(',')
  }

  get maxMegabytes(): number {
    return Math.round(this.maxBytes / (1024 * 1024))
  }

  onDragOver(event: DragEvent) {
    event.preventDefault()
    this.dragging = true
  }

  onDragLeave(event: DragEvent) {
    event.preventDefault()
    this.dragging = false
  }

  onDrop(event: DragEvent) {
    event.preventDefault()
    this.dragging = false
    if (event.dataTransfer?.files) this.addFiles(event.dataTransfer.files)
  }

  onFileInput(event: Event) {
    const input = event.target as HTMLInputElement
    if (input.files) this.addFiles(input.files)
    input.value = '' // allow choosing the same file again
  }

  addFiles(files: FileList | File[]) {
    if (!this.canUpload) return
    for (const file of Array.from(files)) {
      const item: UploadItem = {
        localId: nextLocalId++,
        file,
        previewUrl: null,
        status: 'uploading',
        progress: 0,
        idempotencyKey: newIdempotencyKey(),
      }
      // Pre-validation for quick feedback only; the server decides.
      if (!this.acceptedTypes.includes(file.type)) {
        item.status = 'invalid'
        item.error = $localize`This file type is not supported.`
      } else if (file.size > this.maxBytes) {
        item.status = 'invalid'
        item.error = $localize`The file is larger than ${this.maxMegabytes}:size: MB.`
      } else {
        item.previewUrl = URL.createObjectURL(file)
      }
      this.items = [...this.items, item]
      if (item.status !== 'invalid') this.start(item)
    }
    this.cdr.markForCheck()
  }

  start(item: UploadItem) {
    item.subscription?.unsubscribe()
    item.status = 'uploading'
    item.progress = 0
    item.error = undefined
    item.httpStatus = undefined
    item.subscription = this.imageService
      .upload(item.file, {
        owner: this.owner?.id,
        idempotencyKey: item.idempotencyKey,
      })
      .subscribe({
        next: (event) => {
          if (event.type === HttpEventType.UploadProgress) {
            item.progress = event.total
              ? Math.round((100 * event.loaded) / event.total)
              : item.progress
          } else if (event.type === HttpEventType.Response) {
            item.status = 'done'
            item.progress = 100
            item.result = event.body
            this.uploaded.emit(event.body)
          }
          this.cdr.markForCheck()
        },
        error: (error) => {
          item.status = 'error'
          item.httpStatus =
            error instanceof HttpErrorResponse ? error.status : undefined
          item.error = uploadErrorMessage(error)
          this.cdr.markForCheck()
        },
      })
  }

  /** Aborts the request (unsubscribing cancels the underlying XHR/fetch). */
  cancel(item: UploadItem) {
    item.subscription?.unsubscribe()
    item.status = 'cancelled'
    item.error = undefined
    this.cdr.markForCheck()
  }

  retry(item: UploadItem) {
    this.start(item)
    this.cdr.markForCheck()
  }

  canRetry(item: UploadItem): boolean {
    return (
      (item.status === 'error' || item.status === 'cancelled') &&
      ![403, 413, 415].includes(item.httpStatus)
    )
  }

  canRemove(item: UploadItem): boolean {
    return item.status !== 'done' || !!item.result?.can_delete
  }

  remove(item: UploadItem) {
    item.subscription?.unsubscribe()
    if (item.status === 'done' && item.result) {
      if (!item.result.can_delete) return
      this.imageService.delete(item.result.id).subscribe({
        next: () => {
          this.removed.emit(item.result.id)
          this.drop(item)
        },
        error: (error) => {
          item.error =
            error instanceof HttpErrorResponse && error.status === 403
              ? $localize`You do not have permission to delete this image.`
              : $localize`The image could not be deleted.`
          this.cdr.markForCheck()
        },
      })
    } else {
      this.drop(item)
    }
  }

  private drop(item: UploadItem) {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
    this.items = this.items.filter((i) => i !== item)
    this.cdr.markForCheck()
  }

  ngOnDestroy() {
    for (const item of this.items) {
      item.subscription?.unsubscribe()
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
    }
    this.items = []
  }
}
