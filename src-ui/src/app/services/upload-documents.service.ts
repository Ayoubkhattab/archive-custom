import {
  HttpClient,
  HttpErrorResponse,
  HttpEventType,
  HttpHeaders,
} from '@angular/common/http'
import { Injectable, inject } from '@angular/core'
import { Subscription, firstValueFrom } from 'rxjs'
import { environment } from 'src/environments/environment'
import { DocumentService } from './rest/document.service'
import {
  FileStatus,
  FileStatusPhase,
  WebsocketStatusService,
} from './websocket-status.service'

const MiB = 1024 * 1024

/** What the server reports for a resumable upload (documents/upload_sessions.py). */
interface UploadSession {
  id: string
  chunk_size: number
  total_chunks: number
  received: number[]
  task_id: string | null
}

/**
 * Limits concurrent requests across all uploads. Starts a task synchronously
 * when a slot is free, so a single small upload begins immediately.
 */
class RequestSlots {
  private waiting: Array<() => void> = []

  constructor(private free: number) {}

  run(task: (release: () => void) => void) {
    let released = false
    const release = () => {
      if (released) return
      released = true
      const next = this.waiting.shift()
      if (next) next()
      else this.free++
    }
    if (this.free > 0) {
      this.free--
      task(release)
    } else {
      this.waiting.push(() => task(release))
    }
  }

  acquire(): Promise<() => void> {
    return new Promise((resolve) => this.run(resolve))
  }
}

class UploadInterrupted extends Error {}

export interface UploadOptions {
  /** Id of the folder the file is filed in. */
  folder?: number
}

@Injectable({
  providedIn: 'root',
})
export class UploadDocumentsService {
  private documentService = inject(DocumentService)
  private websocketStatusService = inject(WebsocketStatusService)
  private http = inject(HttpClient)

  private uploadSubscriptions: Array<Subscription> = []

  /** Files larger than this are sent in resumable chunks. */
  chunkedThreshold = 20 * MiB

  /**
   * Requests in flight across all uploads. Browsers allow ~6 connections per
   * host over HTTP/1.1; keeping a couple free lets the rest of the app work
   * while a large upload runs.
   */
  private slots = new RequestSlots(4)

  /** Retries of one request before the upload is reported as interrupted. */
  maxRetries = 10
  retryBaseDelayMs = 1000
  private retryMaxDelayMs = 30_000

  private activeUploads = 0

  private readonly beforeUnload = (event: BeforeUnloadEvent) => {
    // Leaving the page stops the upload (it can be resumed later).
    event.preventDefault()
    event.returnValue = ''
  }

  /**
   * @param options.folder files go into this folder; a ZIP archive is
   *   unpacked into a subfolder of it.
   */
  public uploadFile(file: File, options: UploadOptions = {}) {
    if (file.size > this.chunkedThreshold) {
      this.uploadChunked(file, options)
    } else {
      this.uploadWhole(file, options)
    }
  }

  private uploadWhole(file: File, options: UploadOptions) {
    let formData = new FormData()
    formData.append('document', file, file.name)
    formData.append('from_webui', 'true')
    if (options.folder) formData.append('folder', String(options.folder))
    let status = this.websocketStatusService.newFileUpload(file.name)

    status.message = $localize`Connecting...`

    this.slots.run((release) => {
      this.uploadSubscriptions[file.name] = this.documentService
        .uploadDocument(formData)
        .subscribe({
          next: (event) => {
            if (event.type == HttpEventType.UploadProgress) {
              status.updateProgress(
                FileStatusPhase.UPLOADING,
                event.loaded,
                event.total
              )
              status.message = $localize`Uploading...`
            } else if (event.type == HttpEventType.Response) {
              status.taskId = event.body['task_id'] ?? event.body.toString()
              status.message = $localize`Upload complete, waiting...`
              this.uploadSubscriptions[file.name]?.complete()
              release()
            }
          },
          error: (error) => {
            this.failFromHttpError(status, error)
            this.uploadSubscriptions[file.name]?.complete()
            release()
          },
        })
    })
  }

  private failFromHttpError(status: FileStatus, error: HttpErrorResponse) {
    switch (error.status) {
      case 400: {
        this.websocketStatusService.fail(status, error.error.document)
        break
      }
      default: {
        this.websocketStatusService.fail(
          status,
          error.error?.document ??
            $localize`HTTP error: ${error.status} ${error.statusText}`
        )
        break
      }
    }
  }

  private sessionsUrl(...parts: Array<string | number>) {
    return [`${environment.apiBaseUrl}documents/upload_sessions`, ...parts]
      .join('/')
      .concat('/')
  }

  /**
   * Sends the file in chunks, several at a time. A failed chunk is retried
   * (waiting for the network to come back when offline); if the upload
   * still stops, choosing the same file again resumes it, because the server
   * keeps the chunks and tells which ones it has.
   */
  private async uploadChunked(file: File, options: UploadOptions) {
    const status = this.websocketStatusService.newFileUpload(file.name)
    status.message = $localize`Connecting...`
    this.uploadStarted()

    let sent = 0
    try {
      let session = await this.withRetry(status, () =>
        this.createSession(file)
      )

      if (session.task_id) {
        // Completed before, only the response was lost.
        this.finish(status, session.task_id)
        return
      }

      const chunkLength = (index: number) =>
        Math.min(session.chunk_size, file.size - index * session.chunk_size)
      const received = new Set(session.received)
      sent = [...received].reduce((sum, i) => sum + chunkLength(i), 0)
      const inFlight = new Map<number, number>()
      const report = () => {
        let loaded = sent
        inFlight.forEach((bytes) => (loaded += bytes))
        status.updateProgress(FileStatusPhase.UPLOADING, loaded, file.size)
        status.message = $localize`:@@upload.chunked.progress:Uploading... ${formatBytes(loaded)} of ${formatBytes(file.size)}`
      }

      if (received.size) {
        status.message = $localize`:@@upload.chunked.resuming:Resuming upload from ${Math.floor((sent / file.size) * 100)}%`
      }
      status.updateProgress(FileStatusPhase.UPLOADING, sent, file.size)

      const pending: number[] = []
      for (let i = 0; i < session.total_chunks; i++) {
        if (!received.has(i)) pending.push(i)
      }

      const sendChunk = async (index: number) => {
        await this.withRetry(status, () =>
          this.putChunk(file, session, index, (bytes) => {
            inFlight.set(index, bytes)
            report()
          })
        )
        inFlight.delete(index)
        sent += chunkLength(index)
        report()
      }

      // The server checks the file type on chunk 0: send it alone first so
      // an unsupported file is refused before the rest is sent. A proxy that
      // refuses the chunk size (413) shows up here too: start over with
      // smaller chunks.
      if (pending[0] === 0) {
        pending.shift()
        while (true) {
          try {
            await sendChunk(0)
            break
          } catch (error) {
            if (
              error?.status !== 413 ||
              session.chunk_size / 2 < MiB ||
              received.size
            ) {
              throw error
            }
            await firstValueFrom(this.http.delete(this.sessionsUrl(session.id)))
            session = await this.withRetry(status, () =>
              this.createSession(file, session.chunk_size / 2)
            )
            pending.length = 0
            for (let i = 1; i < session.total_chunks; i++) pending.push(i)
          }
        }
      }

      const worker = async () => {
        while (pending.length) {
          const index = pending.shift()
          const release = await this.slots.acquire()
          try {
            await sendChunk(index)
          } finally {
            release()
          }
        }
      }
      await Promise.all(Array.from({ length: 4 }, () => worker()))

      status.message = $localize`:@@upload.chunked.finishing:Finishing upload...`
      const taskId = await this.withRetry(status, () =>
        firstValueFrom(
          this.http.post<string>(this.sessionsUrl(session.id, 'complete'), {
            from_webui: true,
            ...(options.folder ? { folder: options.folder } : {}),
          })
        )
      )
      this.finish(status, taskId)
    } catch (error) {
      if (error instanceof UploadInterrupted) {
        this.websocketStatusService.fail(
          status,
          $localize`:@@upload.chunked.interrupted:Upload interrupted at ${Math.floor((sent / file.size) * 100)}%. Select the same file again to resume.`
        )
      } else if (error?.status === 413 && !error.error?.document) {
        this.websocketStatusService.fail(
          status,
          $localize`:@@upload.chunked.proxyLimit:The web server in front of the archive refused the upload (413). Allow larger requests there (nginx: client_max_body_size) or lower PAPERLESS_UPLOAD_CHUNK_SIZE_MB.`
        )
      } else if (error instanceof HttpErrorResponse) {
        this.failFromHttpError(status, error)
      } else {
        this.websocketStatusService.fail(status, String(error))
      }
    } finally {
      this.uploadFinished()
    }
  }

  private finish(status: FileStatus, taskId: string) {
    status.updateProgress(FileStatusPhase.UPLOADING, 1, 1)
    status.taskId = taskId
    status.message = $localize`Upload complete, waiting...`
  }

  private createSession(file: File, chunkSize?: number) {
    return firstValueFrom(
      this.http.post<UploadSession>(this.sessionsUrl(), {
        filename: file.name,
        size: file.size,
        fingerprint: `${file.name}:${file.size}:${file.lastModified}`,
        ...(chunkSize ? { chunk_size: chunkSize } : {}),
      })
    )
  }

  private putChunk(
    file: File,
    session: UploadSession,
    index: number,
    onProgress: (bytes: number) => void
  ): Promise<void> {
    const start = index * session.chunk_size
    const blob = file.slice(start, start + session.chunk_size)
    return new Promise((resolve, reject) => {
      this.http
        .put(this.sessionsUrl(session.id, 'chunks', index), blob, {
          headers: new HttpHeaders({
            'Content-Type': 'application/octet-stream',
          }),
          reportProgress: true,
          observe: 'events',
        })
        .subscribe({
          next: (event) => {
            if (event.type == HttpEventType.UploadProgress) {
              onProgress(event.loaded)
            } else if (event.type == HttpEventType.Response) {
              resolve()
            }
          },
          error: (error) => {
            onProgress(0)
            reject(error)
          },
        })
    })
  }

  private isRetryable(error: HttpErrorResponse) {
    // 0: network error, connection reset, server restarting.
    return [0, 408, 409, 423, 429, 500, 502, 503, 504].includes(error?.status)
  }

  /**
   * Runs a request, retrying transient failures with increasing waits.
   * While the browser is offline it waits for the connection instead of
   * using up retries.
   */
  private async withRetry<T>(
    status: FileStatus,
    request: () => Promise<T>
  ): Promise<T> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await request()
      } catch (error) {
        // 409 from "complete" is a race with another tab: retrying settles it.
        if (!this.isRetryable(error)) throw error
        if (attempt >= this.maxRetries) throw new UploadInterrupted()
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
          status.message = $localize`:@@upload.chunked.offline:No connection. The upload continues when it is back...`
          await waitForOnline()
          attempt = -1
          continue
        }
        status.message = $localize`:@@upload.chunked.retrying:Connection lost, retrying...`
        await sleep(
          Math.min(
            this.retryMaxDelayMs,
            this.retryBaseDelayMs * 2 ** attempt
          )
        )
      }
    }
  }

  private uploadStarted() {
    if (this.activeUploads++ === 0 && typeof window !== 'undefined') {
      window.addEventListener('beforeunload', this.beforeUnload)
    }
  }

  private uploadFinished() {
    if (--this.activeUploads === 0 && typeof window !== 'undefined') {
      window.removeEventListener('beforeunload', this.beforeUnload)
    }
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function waitForOnline(): Promise<void> {
  return new Promise((resolve) =>
    window.addEventListener('online', () => resolve(), { once: true })
  )
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  return `${Math.round(bytes / MiB)} MB`
}
