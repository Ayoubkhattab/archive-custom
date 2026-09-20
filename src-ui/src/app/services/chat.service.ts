import {
  HttpClient,
  HttpDownloadProgressEvent,
  HttpEventType,
} from '@angular/common/http'
import { inject, Injectable } from '@angular/core'
import { filter, map, Observable } from 'rxjs'
import { AiMode } from 'src/app/data/ai-conversation'
import { environment } from 'src/environments/environment'

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
}
