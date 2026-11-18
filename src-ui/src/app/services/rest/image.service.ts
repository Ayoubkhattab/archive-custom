import { HttpClient, HttpEvent, HttpParams } from '@angular/common/http'
import { inject, Injectable } from '@angular/core'
import { Observable } from 'rxjs'
import { timeout } from 'rxjs/operators'
import { Results } from 'src/app/data/results'
import { UploadedImage } from 'src/app/data/uploaded-image'
import { environment } from 'src/environments/environment'

/** Inactivity timeout for uploads (progress events reset it). */
export const UPLOAD_TIMEOUT_MS = 60000
export const REQUEST_TIMEOUT_MS = 20000

@Injectable({ providedIn: 'root' })
export class ImageService {
  private http = inject(HttpClient)
  private baseUrl = `${environment.apiBaseUrl}images/`

  upload(
    file: File,
    options: { owner?: number; altText?: string; idempotencyKey: string }
  ): Observable<HttpEvent<UploadedImage>> {
    const form = new FormData()
    form.append('file', file, file.name)
    if (options.owner) form.append('owner', String(options.owner))
    if (options.altText) form.append('alt_text', options.altText)
    form.append('idempotency_key', options.idempotencyKey)
    return this.http
      .post<UploadedImage>(this.baseUrl, form, {
        reportProgress: true,
        observe: 'events',
      })
      .pipe(timeout({ each: UPLOAD_TIMEOUT_MS }))
  }

  list(page = 1, pageSize = 25): Observable<Results<UploadedImage>> {
    const params = new HttpParams()
      .set('page', page)
      .set('page_size', pageSize)
    return this.http
      .get<Results<UploadedImage>>(this.baseUrl, { params })
      .pipe(timeout(REQUEST_TIMEOUT_MS))
  }

  get(id: string): Observable<UploadedImage> {
    return this.http
      .get<UploadedImage>(`${this.baseUrl}${id}/`)
      .pipe(timeout(REQUEST_TIMEOUT_MS))
  }

  delete(id: string): Observable<void> {
    return this.http
      .delete<void>(`${this.baseUrl}${id}/`)
      .pipe(timeout(REQUEST_TIMEOUT_MS))
  }
}
