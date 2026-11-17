import { HttpClient } from '@angular/common/http'
import { inject, Injectable } from '@angular/core'
import { BehaviorSubject, Observable, tap } from 'rxjs'
import { Folder } from 'src/app/data/folder'
import { environment } from 'src/environments/environment'

/**
 * The folder tree. The server returns every visible folder at once (folders
 * are few), and the last list is kept so pages can share it.
 */
@Injectable({
  providedIn: 'root',
})
export class FolderService {
  private http = inject(HttpClient)
  private url = `${environment.apiBaseUrl}folders/`

  private folders = new BehaviorSubject<Folder[] | null>(null)

  /** The last loaded list, or null before the first load. */
  readonly folders$ = this.folders.asObservable()

  list(): Observable<Folder[]> {
    return this.http
      .get<Folder[]>(this.url)
      .pipe(tap((folders) => this.folders.next(folders)))
  }

  create(name: string, parent: number | null): Observable<Folder> {
    return this.http
      .post<Folder>(this.url, { name, parent })
      .pipe(tap(() => this.reload()))
  }

  update(id: number, changes: Partial<Folder>): Observable<Folder> {
    return this.http
      .patch<Folder>(`${this.url}${id}/`, changes)
      .pipe(tap(() => this.reload()))
  }

  delete(id: number): Observable<unknown> {
    return this.http.delete(`${this.url}${id}/`).pipe(tap(() => this.reload()))
  }

  /** Refreshes the shared list in the background. */
  reload(): void {
    this.list().subscribe({ error: () => {} })
  }
}
