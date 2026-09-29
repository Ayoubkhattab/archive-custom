import { Injectable, inject } from '@angular/core'
import { Document } from '../data/document'
import { SettingsService } from './settings.service'

export interface RecentDocument {
  id: number
  title: string
  created?: Date | string
  visited: number // epoch ms
}

const STORAGE_KEY_PREFIX = 'pngx-recent-documents'
const MAX_RECENT_DOCUMENTS = 20

/**
 * Remembers the documents the current user opened most recently, so the
 * dashboard and the command palette can offer "continue where you left off"
 * without a round trip to the server.
 *
 * Kept in localStorage and namespaced by user id, so two people sharing a
 * browser do not see each other's history. Only id, title and date are kept.
 */
@Injectable({
  providedIn: 'root',
})
export class RecentDocumentsService {
  private settingsService = inject(SettingsService)

  // A plain array rather than a signal: it is loaded lazily from inside
  // template reads, where writing a signal is not allowed.
  private items: RecentDocument[] = []

  private loadedForKey: string = null

  private get storageKey(): string {
    const userId = this.settingsService.currentUser?.id
    return userId != null
      ? `${STORAGE_KEY_PREFIX}-${userId}`
      : STORAGE_KEY_PREFIX
  }

  /** Current list, (re)loaded lazily once the user is known. */
  list(): RecentDocument[] {
    this.ensureLoaded()
    return this.items
  }

  record(doc: Pick<Document, 'id' | 'title' | 'created'>) {
    if (!doc?.id) return
    this.ensureLoaded()
    const entry: RecentDocument = {
      id: doc.id,
      title: doc.title,
      created: doc.created,
      visited: Date.now(),
    }
    this.items = [entry, ...this.items.filter((d) => d.id !== doc.id)].slice(
      0,
      MAX_RECENT_DOCUMENTS
    )
    this.save()
  }

  remove(id: number) {
    this.ensureLoaded()
    this.items = this.items.filter((d) => d.id !== id)
    this.save()
  }

  clear() {
    this.items = []
    this.save()
  }

  private ensureLoaded() {
    const key = this.storageKey
    if (this.loadedForKey === key) return
    this.loadedForKey = key
    try {
      const raw = localStorage.getItem(key)
      const parsed = raw ? JSON.parse(raw) : []
      this.items = Array.isArray(parsed)
        ? parsed.filter((d) => typeof d?.id === 'number')
        : []
    } catch {
      this.items = []
    }
  }

  private save() {
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(this.items))
    } catch {
      // Storage full or blocked (private mode): history is a convenience,
      // so failing silently is the right behaviour.
    }
  }
}
