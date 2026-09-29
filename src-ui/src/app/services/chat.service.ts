import {
  HttpClient,
  HttpDownloadProgressEvent,
  HttpEventType,
  HttpHeaderResponse,
} from '@angular/common/http'
import { inject, Injectable } from '@angular/core'
import { filter, map, Observable } from 'rxjs'
import { AiMode } from 'src/app/data/ai-conversation'
import { environment } from 'src/environments/environment'

/** One update of a streamed "ask about this file" answer. */
export interface DocumentAskChunk {
  /** Everything received so far (not just the new part). */
  text?: string
  /** Whether the server read the whole document or excerpts of it. */
  context?: 'complete' | 'excerpts'
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  isStreaming?: boolean
}

@Injectable({
  providedIn: 'root',
})
export class ChatService {
  private http: HttpClient = inject(HttpClient)

  /**
   * @param mode How to answer. Left out for the short default the navbar chat
   *   uses, so the request is unchanged for existing callers.
   */
  streamChat(
    documentId: number,
    prompt: string,
    mode?: AiMode
  ): Observable<string> {
    return this.http
      .post(
        `${environment.apiBaseUrl}documents/chat/`,
        {
          document_id: documentId,
          q: prompt,
          ...(mode ? { mode } : {}),
        },
        {
          observe: 'events',
          reportProgress: true,
          responseType: 'text',
          withCredentials: true,
        }
      )
      .pipe(
        map((event) => {
          if (event.type === HttpEventType.DownloadProgress) {
            return (event as HttpDownloadProgressEvent).partialText!
          }
        }),
        filter((chunk) => !!chunk)
      )
  }

  /**
   * "Ask about this file": a conversation about one document, answered from
   * its own text. Separate from `streamChat`, which serves the general chat.
   *
   * @param history earlier turns of this conversation, oldest first.
   */
  askDocument(
    documentId: number,
    question: string,
    history: ChatMessage[] = [],
    mode?: AiMode
  ): Observable<DocumentAskChunk> {
    return this.http
      .post(
        `${environment.apiBaseUrl}documents/ask/`,
        {
          document_id: documentId,
          q: question,
          history: history.map(({ role, content }) => ({ role, content })),
          ...(mode ? { mode } : {}),
        },
        {
          observe: 'events',
          reportProgress: true,
          responseType: 'text',
          withCredentials: true,
        }
      )
      .pipe(
        map((event): DocumentAskChunk => {
          if (event.type === HttpEventType.ResponseHeader) {
            const context = (event as HttpHeaderResponse).headers.get(
              'X-Document-Context'
            )
            return context === 'complete' || context === 'excerpts'
              ? { context }
              : null
          }
          if (event.type === HttpEventType.DownloadProgress) {
            const text = (event as HttpDownloadProgressEvent).partialText
            return text ? { text } : null
          }
          return null
        }),
        filter((chunk) => !!chunk)
      )
  }

  /**
   * Ask the server to have the model pre-read a document for "ask about this
   * file", so the first question doesn't wait for it. Fire and forget: it
   * answers at once and does nothing for documents that are too long.
   */
  warmDocument(documentId: number): Observable<unknown> {
    return this.http.post(
      `${environment.apiBaseUrl}documents/ask/warm/`,
      { document_id: documentId },
      { withCredentials: true }
    )
  }
}
