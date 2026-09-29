import { HttpClient, HttpErrorResponse } from '@angular/common/http'
import { Injectable, inject } from '@angular/core'
import { Observable, catchError, map, throwError } from 'rxjs'
import { environment } from 'src/environments/environment'
import {
  describeHttpFailure,
  isProxyErrorBody,
  stripHeartbeats,
} from '../utils/stream-errors'

export interface AiClassification {
  title?: string
  tags?: string[]
  correspondents?: string[]
  document_types?: string[]
  storage_paths?: string[]
  dates?: string[]
}

export interface AiClassificationResult {
  classification: AiClassification
  /** Came from the server's cache rather than a new model call. */
  cached: boolean
  applied: boolean
}

/** A failure already worded for the person (Arabic, from the server or here). */
export class AiSuggestionError extends Error {}

/**
 * AI suggestions for one document (title, tags, correspondent, type…).
 *
 * The server streams heartbeats while the model works and sends the JSON at
 * the end, so a slow model on a CPU no longer runs into Cloudflare's 524
 * timeout. The heartbeats are stripped here before parsing.
 */
@Injectable({
  providedIn: 'root',
})
export class AiSuggestionService {
  private http = inject(HttpClient)

  classify(
    documentId: number,
    apply: boolean = false
  ): Observable<AiClassificationResult> {
    return this.http
      .post(
        `${environment.apiBaseUrl}documents/ai_suggest/classify/`,
        { document_id: documentId, apply },
        { responseType: 'text', withCredentials: true }
      )
      .pipe(
        map((body) => this.parse(body)),
        catchError((error) => throwError(() => this.describe(error)))
      )
  }

  private parse(body: string): AiClassificationResult {
    const text = stripHeartbeats(body ?? '').trim()
    if (!text || isProxyErrorBody(text)) {
      throw new AiSuggestionError(describeHttpFailure(524))
    }
    let payload: any
    try {
      payload = JSON.parse(text)
    } catch {
      throw new AiSuggestionError(describeHttpFailure(undefined))
    }
    if (!payload?.success) {
      throw new AiSuggestionError(
        payload?.error || describeHttpFailure(undefined)
      )
    }
    return {
      classification: payload.classification ?? payload.suggestions ?? {},
      cached: !!payload.cached,
      applied: !!payload.applied,
    }
  }

  private describe(error: unknown): AiSuggestionError {
    if (error instanceof AiSuggestionError) return error
    if (error instanceof HttpErrorResponse) {
      // Refusals before the model runs (permissions, rate limit, AI off) are
      // plain JSON with a status code.
      if (error.status === 400 && typeof error.error === 'string') {
        try {
          const detail = JSON.parse(error.error)?.error
          if (detail === 'AI is not enabled') {
            return new AiSuggestionError(
              $localize`:@@ask.error.aiDisabled:AI features are turned off in the configuration.`
            )
          }
        } catch {
          // fall through to the generic message
        }
      }
      return new AiSuggestionError(describeHttpFailure(error.status))
    }
    return new AiSuggestionError(describeHttpFailure(undefined))
  }
}
