import { LocationStrategy } from '@angular/common'
import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  ViewChild,
  inject,
} from '@angular/core'
import { FormsModule } from '@angular/forms'
import { Params, Router } from '@angular/router'
import { NgbActiveModal } from '@ng-bootstrap/ng-bootstrap'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import {
  Subject,
  catchError,
  debounceTime,
  distinctUntilChanged,
  of,
  switchMap,
  takeUntil,
} from 'rxjs'
import {
  FILTER_FULLTEXT_QUERY,
  FILTER_HAS_CORRESPONDENT_ANY,
  FILTER_HAS_DOCUMENT_TYPE_ANY,
  FILTER_HAS_STORAGE_PATH_ANY,
  FILTER_HAS_TAGS_ALL,
  FILTER_TITLE_CONTENT,
} from 'src/app/data/filter-rule-type'
import { GlobalSearchType, SETTINGS_KEYS } from 'src/app/data/ui-settings'
import { CustomDatePipe } from 'src/app/pipes/custom-date.pipe'
import { DocumentAskService } from 'src/app/services/document-ask.service'
import { DocumentListViewService } from 'src/app/services/document-list-view.service'
import { HotKeyService } from 'src/app/services/hot-key.service'
import {
  PermissionAction,
  PermissionsService,
  PermissionType,
} from 'src/app/services/permissions.service'
import { RecentDocumentsService } from 'src/app/services/recent-documents.service'
import { DocumentService } from 'src/app/services/rest/document.service'
import { SavedViewService } from 'src/app/services/rest/saved-view.service'
import {
  GlobalSearchResult,
  SearchService,
} from 'src/app/services/rest/search.service'
import { SettingsService } from 'src/app/services/settings.service'
import { UploadDocumentsService } from 'src/app/services/upload-documents.service'
import { matchesQuery } from 'src/app/utils/arabic-text'

export interface PaletteItem {
  id: string
  icon: string
  label: string
  /** Extra text that is searched but not shown (synonyms, English names). */
  keywords?: string
  hint?: string
  date?: Date | string
  run: (newWindow: boolean) => void
  /** Assigned when groups are built; position in keyboard order. */
  index?: number
}

export interface PaletteGroup {
  key: string
  label: string
  items: PaletteItem[]
}

const MAX_RECENT = 6
const MAX_PER_REMOTE_GROUP = 5

@Component({
  selector: 'pngx-command-palette',
  templateUrl: './command-palette.component.html',
  styleUrl: './command-palette.component.scss',
  imports: [FormsModule, NgxBootstrapIconsModule, CustomDatePipe],
})
export class CommandPaletteComponent implements AfterViewInit, OnDestroy {
  private activeModal = inject(NgbActiveModal)
  private router = inject(Router)
  private locationStrategy = inject(LocationStrategy)
  private permissionsService = inject(PermissionsService)
  private settingsService = inject(SettingsService)
  private savedViewService = inject(SavedViewService)
  private searchService = inject(SearchService)
  private documentService = inject(DocumentService)
  private documentListViewService = inject(DocumentListViewService)
  private recentDocumentsService = inject(RecentDocumentsService)
  private uploadDocumentsService = inject(UploadDocumentsService)
  private hotKeyService = inject(HotKeyService)
  private documentAskService = inject(DocumentAskService)

  @ViewChild('searchInput') searchInput: ElementRef<HTMLInputElement>
  @ViewChild('fileInput') fileInput: ElementRef<HTMLInputElement>
  @ViewChild('list') list: ElementRef<HTMLElement>

  public query: string = ''
  public groups: PaletteGroup[] = []
  public activeIndex: number = 0
  public loading: boolean = false

  private flatItems: PaletteItem[] = []
  private remote: GlobalSearchResult = null
  private queryChanged = new Subject<string>()
  private unsubscribeNotifier = new Subject<void>()

  constructor() {
    this.queryChanged
      .pipe(
        debounceTime(250),
        distinctUntilChanged(),
        switchMap((q) => {
          const trimmed = q.trim()
          if (trimmed.length < 3) {
            this.loading = false
            return of(null)
          }
          this.loading = true
          return this.searchService
            .globalSearch(trimmed)
            .pipe(catchError(() => of(null)))
        }),
        takeUntil(this.unsubscribeNotifier)
      )
      .subscribe((results: GlobalSearchResult) => {
        this.loading = false
        this.remote = results
        this.rebuild()
      })
    this.rebuild()
  }

  ngAfterViewInit(): void {
    // Focus after the modal's own focus handling has run.
    setTimeout(() => this.searchInput?.nativeElement.focus())
  }

  ngOnDestroy(): void {
    this.unsubscribeNotifier.next()
    this.unsubscribeNotifier.complete()
  }

  public setQuery(query: string) {
    this.query = query
    this.onQueryChange(query)
  }

  public onQueryChange(query: string) {
    // Remote results belong to the previous query; drop them at once so
    // stale documents never sit under a new query.
    this.remote = null
    this.rebuild()
    this.queryChanged.next(query ?? '')
  }

  public get activeItemId(): string {
    const item = this.flatItems[this.activeIndex]
    return item ? `palette-item-${item.id}` : null
  }

  public onKeydown(event: KeyboardEvent) {
    const count = this.flatItems.length
    switch (event.key) {
      case 'ArrowDown':
        if (count) this.setActive((this.activeIndex + 1) % count)
        event.preventDefault()
        break
      case 'ArrowUp':
        if (count) this.setActive((this.activeIndex - 1 + count) % count)
        event.preventDefault()
        break
      case 'Home':
        if (event.ctrlKey && count) {
          this.setActive(0)
          event.preventDefault()
        }
        break
      case 'End':
        if (event.ctrlKey && count) {
          this.setActive(count - 1)
          event.preventDefault()
        }
        break
      case 'Enter': {
        const item = this.flatItems[this.activeIndex]
        if (item) {
          event.preventDefault()
          this.run(item, event.ctrlKey || event.metaKey)
        }
        break
      }
    }
  }

  public onItemClick(item: PaletteItem, event: MouseEvent) {
    this.run(item, event.ctrlKey || event.metaKey)
  }

  public onItemHover(item: PaletteItem) {
    this.activeIndex = item.index
  }

  public run(item: PaletteItem, newWindow: boolean = false) {
    item.run(newWindow)
  }

  public close() {
    this.activeModal.dismiss()
  }

  public onFilesPicked(event: Event) {
    const input = event.target as HTMLInputElement
    const files = Array.from(input.files ?? [])
    files.forEach((file) => this.uploadDocumentsService.uploadFile(file))
    input.value = ''
    if (files.length) this.activeModal.close()
  }

  private setActive(index: number) {
    this.activeIndex = index
    const id = this.activeItemId
    setTimeout(() => {
      this.list?.nativeElement
        .querySelector(`#${id}`)
        ?.scrollIntoView({ block: 'nearest' })
    })
  }

  // Building -----------------------------------------------------------------

  private rebuild() {
    const q = this.query?.trim() ?? ''
    const groups: PaletteGroup[] = []

    const recent = this.recentItems().filter((i) =>
      matchesQuery(i.label, q)
    )
    const views = this.savedViewItems().filter((i) =>
      matchesQuery(`${i.label} ${i.keywords ?? ''}`, q)
    )
    const pages = this.pageItems().filter((i) =>
      matchesQuery(`${i.label} ${i.keywords ?? ''}`, q)
    )
    const actions = this.actionItems().filter((i) =>
      matchesQuery(`${i.label} ${i.keywords ?? ''}`, q)
    )

    if (q.length) {
      groups.push({
        key: 'search',
        label: $localize`:@@palette.group.search:Search`,
        items: [this.fullTextSearchItem(q)],
      })
      const remoteDocs = this.remoteDocumentItems()
      if (remoteDocs.length) {
        groups.push({
          key: 'documents',
          label: $localize`Documents`,
          items: remoteDocs,
        })
      }
    }

    if (recent.length) {
      groups.push({
        key: 'recent',
        label: $localize`:@@palette.group.recent:Recently opened`,
        items: q.length ? recent : recent.slice(0, MAX_RECENT),
      })
    }
    if (views.length) {
      groups.push({
        key: 'views',
        label: $localize`Saved views`,
        items: views,
      })
    }
    if (q.length) {
      const filters = this.remoteFilterItems()
      if (filters.length) {
        groups.push({
          key: 'filters',
          label: $localize`:@@palette.group.filters:Filter documents by`,
          items: filters,
        })
      }
    }
    if (actions.length) {
      groups.push({
        key: 'actions',
        label: $localize`:@@palette.group.actions:Quick actions`,
        items: actions,
      })
    }
    if (pages.length) {
      groups.push({
        key: 'pages',
        label: $localize`:@@palette.group.pages:Go to`,
        items: pages,
      })
    }

    let index = 0
    this.flatItems = []
    for (const group of groups) {
      for (const item of group.items) {
        item.index = index++
        this.flatItems.push(item)
      }
    }
    this.groups = groups
    this.activeIndex = 0
  }

  private can(action: PermissionAction, type: PermissionType): boolean {
    return this.permissionsService.currentUserCan(action, type)
  }

  private navigate(
    commands: any[],
    newWindow: boolean,
    queryParams: Params = undefined
  ) {
    if (newWindow) {
      const serializedUrl = this.router.serializeUrl(
        this.router.createUrlTree(commands, { queryParams })
      )
      const baseHref = this.locationStrategy.getBaseHref()
      window.open(
        baseHref.replace(/\/+$/, '') +
          '/' +
          serializedUrl.replace(/^\/+/, ''),
        '_blank'
      )
    } else {
      this.activeModal.close()
      this.router.navigate(commands, { queryParams })
    }
  }

  private recentItems(): PaletteItem[] {
    if (!this.can(PermissionAction.View, PermissionType.Document)) return []
    return this.recentDocumentsService.list().map((doc) => ({
      id: `recent-${doc.id}`,
      icon: 'clock-history',
      label: doc.title,
      date: doc.created,
      run: (newWindow) => this.navigate(['/documents', doc.id], newWindow),
    }))
  }

  private savedViewItems(): PaletteItem[] {
    if (!this.can(PermissionAction.View, PermissionType.SavedView)) return []
    return (this.savedViewService.allViews ?? []).map((view) => ({
      id: `view-${view.id}`,
      icon: 'funnel',
      label: view.name,
      run: (newWindow) => this.navigate(['/view', view.id], newWindow),
    }))
  }

  private fullTextSearchItem(q: string): PaletteItem {
    const advanced =
      this.settingsService.get(SETTINGS_KEYS.SEARCH_FULL_TYPE) ===
      GlobalSearchType.ADVANCED
    return {
      id: 'full-search',
      icon: 'search',
      label: `${$localize`:@@palette.searchAll:Search all documents for`} «${q}»`,
      run: () => {
        this.activeModal.close()
        this.documentService.searchQuery = advanced ? q : ''
        this.documentListViewService.quickFilter([
          {
            rule_type: advanced ? FILTER_FULLTEXT_QUERY : FILTER_TITLE_CONTENT,
            value: q,
          },
        ])
      },
    }
  }

  private remoteDocumentItems(): PaletteItem[] {
    return (this.remote?.documents ?? [])
      .slice(0, MAX_PER_REMOTE_GROUP)
      .map((doc) => ({
        id: `doc-${doc.id}`,
        icon: 'file-earmark-text',
        label: doc.title,
        date: doc.created,
        run: (newWindow) => this.navigate(['/documents', doc.id], newWindow),
      }))
  }

  private remoteFilterItems(): PaletteItem[] {
    if (!this.remote) return []
    const filterBy = (ruleType: number, id: number) => () => {
      this.activeModal.close()
      this.documentListViewService.quickFilter([
        { rule_type: ruleType, value: id.toString() },
      ])
    }
    const correspondent = $localize`Correspondent`
    const documentType = $localize`Document type`
    const storagePath = $localize`Storage path`
    const tag = $localize`:@@palette.hint.tag:Tag`
    return [
      ...(this.remote.correspondents ?? []).map((o) => ({
        id: `corr-${o.id}`,
        icon: 'person',
        label: o.name,
        hint: correspondent,
        run: filterBy(FILTER_HAS_CORRESPONDENT_ANY, o.id),
      })),
      ...(this.remote.document_types ?? []).map((o) => ({
        id: `dt-${o.id}`,
        icon: 'hash',
        label: o.name,
        hint: documentType,
        run: filterBy(FILTER_HAS_DOCUMENT_TYPE_ANY, o.id),
      })),
      ...(this.remote.tags ?? []).map((o) => ({
        id: `tag-${o.id}`,
        icon: 'tag',
        label: o.name,
        hint: tag,
        run: filterBy(FILTER_HAS_TAGS_ALL, o.id),
      })),
      ...(this.remote.storage_paths ?? []).map((o) => ({
        id: `sp-${o.id}`,
        icon: 'folder',
        label: o.name,
        hint: storagePath,
        run: filterBy(FILTER_HAS_STORAGE_PATH_ANY, o.id),
      })),
    ].slice(0, MAX_PER_REMOTE_GROUP * 2)
  }

  private actionItems(): PaletteItem[] {
    const items: PaletteItem[] = []
    const currentDocumentId = +(
      this.router.url.match(/^\/documents\/(\d+)/)?.[1] ?? 0
    )
    if (
      currentDocumentId &&
      this.settingsService.get(SETTINGS_KEYS.AI_ENABLED)
    ) {
      items.push({
        id: 'action-ask-document',
        icon: 'stars',
        label: $localize`:@@palette.action.askDocument:Ask the AI about this document`,
        keywords: 'ai ask question chat document ذكاء اسال سؤال ملف مستند',
        run: () => {
          this.activeModal.close()
          this.documentAskService.open(currentDocumentId)
        },
      })
    }
    if (this.can(PermissionAction.Add, PermissionType.Document)) {
      items.push({
        id: 'action-upload',
        icon: 'cloud-arrow-up',
        label: $localize`:@@palette.action.upload:Upload documents`,
        keywords: 'upload add new file رفع اضافه ملف جديد',
        run: () => this.fileInput?.nativeElement.click(),
      })
    }
    if (this.can(PermissionAction.View, PermissionType.Document)) {
      items.push({
        id: 'action-recently-added',
        icon: 'calendar-week',
        label: $localize`:@@palette.action.recentlyAdded:Recently added documents`,
        keywords: 'recent latest new added احدث جديد',
        run: (newWindow) =>
          this.navigate(['/documents'], newWindow, {
            sort: 'added',
            reverse: 1,
          }),
      })
    }
    items.push({
      id: 'action-theme',
      icon: this.settingsService.get(SETTINGS_KEYS.DARK_MODE_ENABLED)
        ? 'sun'
        : 'moon-stars',
      label: $localize`:@@palette.action.theme:Switch light / dark mode`,
      keywords: 'theme dark light night mode مظهر ليلي نهاري داكن فاتح',
      run: () => {
        this.activeModal.close()
        this.settingsService.toggleDarkMode()
      },
    })
    items.push({
      id: 'action-shortcuts',
      icon: 'keyboard',
      label: $localize`:@@palette.action.shortcuts:Keyboard shortcuts`,
      keywords: 'keyboard shortcuts hotkeys help اختصارات مساعده',
      run: () => {
        this.activeModal.close()
        this.hotKeyService.openHelpModal()
      },
    })
    if (this.recentDocumentsService.list().length) {
      items.push({
        id: 'action-clear-recent',
        icon: 'x-circle',
        label: $localize`:@@palette.action.clearRecent:Clear recently opened list`,
        keywords: 'clear history recent مسح سجل',
        run: () => {
          this.recentDocumentsService.clear()
          this.rebuild()
        },
      })
    }
    return items
  }

  private pageItems(): PaletteItem[] {
    const page = (
      id: string,
      icon: string,
      label: string,
      route: string,
      keywords: string,
      visible: boolean = true
    ): PaletteItem =>
      visible
        ? {
            id: `page-${id}`,
            icon,
            label,
            keywords,
            run: (newWindow) => this.navigate([route], newWindow),
          }
        : null

    const A = PermissionAction
    const T = PermissionType
    return [
      page('dashboard', 'house', $localize`Dashboard`, '/dashboard', 'dashboard home الرئيسيه لوحه'),
      page('documents', 'files', $localize`Documents`, '/documents', 'documents all المستندات', this.can(A.View, T.Document)),
      page('ai', 'chat-square-dots', $localize`AI chat`, '/ai', 'ai chat assistant ذكاء محادثه', this.settingsService.get(SETTINGS_KEYS.AI_ENABLED)),
      page('correspondents', 'person', $localize`Correspondents`, '/correspondents', 'correspondents', this.can(A.View, T.Correspondent)),
      page('tags', 'tags', $localize`Tags`, '/tags', 'tags labels وسوم', this.can(A.View, T.Tag)),
      page('documenttypes', 'hash', $localize`Document Types`, '/documenttypes', 'document types', this.can(A.View, T.DocumentType)),
      page('storagepaths', 'folder', $localize`Storage Paths`, '/storagepaths', 'storage paths folders', this.can(A.View, T.StoragePath)),
      page('customfields', 'ui-radios', $localize`Custom Fields`, '/customfields', 'custom fields', this.can(A.View, T.CustomField)),
      page('savedviews', 'window-stack', $localize`Saved Views`, '/savedviews', 'saved views', this.can(A.View, T.SavedView)),
      page('workflows', 'boxes', $localize`Workflows`, '/workflows', 'workflows automation', this.can(A.View, T.Workflow)),
      page('mail', 'envelope', $localize`Mail`, '/mail', 'mail email بريد', this.can(A.View, T.MailAccount)),
      page('trash', 'trash', $localize`Trash`, '/trash', 'trash deleted محذوفات سله', this.can(A.Delete, T.Document)),
      page('settings', 'gear', $localize`Settings`, '/settings', 'settings preferences اعدادات', this.can(A.Change, T.UISettings)),
      page('config', 'sliders2-vertical', $localize`Configuration`, '/config', 'configuration config تكوين', this.can(A.Change, T.AppConfig)),
      page('usersgroups', 'people', $localize`Users & Groups`, '/usersgroups', 'users groups مستخدمين مجموعات', this.can(A.View, T.User)),
      page('tasks', 'list-task', $localize`File Tasks`, '/tasks', 'tasks queue مهام', this.can(A.View, T.PaperlessTask)),
      page('logs', 'text-left', $localize`Logs`, '/logs', 'logs سجلات', this.permissionsService.isAdmin()),
    ].filter(Boolean)
  }
}
