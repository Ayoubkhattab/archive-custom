import {
  Component,
  EventEmitter,
  inject,
  Input,
  OnDestroy,
  OnInit,
  Output,
} from '@angular/core'
import { FormsModule } from '@angular/forms'
import { NgSelectModule } from '@ng-select/ng-select'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { of, Subject } from 'rxjs'
import {
  catchError,
  debounceTime,
  map,
  switchMap,
  takeUntil,
} from 'rxjs/operators'
import { Correspondent } from 'src/app/data/correspondent'
import { DocumentClassification } from 'src/app/data/document-classification'
import { FilterRule } from 'src/app/data/filter-rule'
import {
  PermissionAction,
  PermissionsService,
  PermissionType,
} from 'src/app/services/permissions.service'
import { CorrespondentService } from 'src/app/services/rest/correspondent.service'
import { DocumentClassificationService } from 'src/app/services/rest/document-classification.service'
import { DocumentService } from 'src/app/services/rest/document.service'
import {
  AiSearchScope,
  aiSearchScopeIsEmpty,
  aiSearchScopeToParams,
  emptyAiSearchScope,
} from 'src/app/utils/ai-search-scope'
import { FilterEditorComponent } from '../../document-list/filter-editor/filter-editor.component'

/**
 * The "customise the search" panel of the AI page: the document list's own
 * filter editor plus the official-correspondence fields, and how many
 * documents the current choice leaves for the assistant to read.
 */
@Component({
  selector: 'pngx-ai-scope',
  templateUrl: './ai-scope.component.html',
  styleUrls: ['./ai-scope.component.scss'],
  imports: [
    FilterEditorComponent,
    FormsModule,
    NgSelectModule,
    NgxBootstrapIconsModule,
  ],
})
export class AiScopeComponent implements OnInit, OnDestroy {
  private documentService = inject(DocumentService)
  private correspondentService = inject(CorrespondentService)
  private classificationService = inject(DocumentClassificationService)
  private permissionsService = inject(PermissionsService)

  /** Never changes, so the filter editor's "reset" compares against nothing. */
  readonly noRules: FilterRule[] = []

  private _scope: AiSearchScope = emptyAiSearchScope()

  @Input()
  set scope(value: AiSearchScope) {
    this._scope = value ?? emptyAiSearchScope()
    this.countRequests.next(this._scope)
  }

  get scope(): AiSearchScope {
    return this._scope
  }

  @Output() scopeChange = new EventEmitter<AiSearchScope>()

  @Input() disabled = false

  correspondents: Correspondent[] = []
  classifications: DocumentClassification[] = []
  canViewCorrespondents = false

  /** Documents the current filters leave; null while counting or unknown. */
  matchCount: number | null = null
  counting = false
  countFailed = false

  private countRequests = new Subject<AiSearchScope>()
  private unsubscribeNotifier = new Subject<void>()

  get isEmpty(): boolean {
    return aiSearchScopeIsEmpty(this._scope)
  }

  ngOnInit(): void {
    this.canViewCorrespondents = this.permissionsService.currentUserCan(
      PermissionAction.View,
      PermissionType.Correspondent
    )
    if (this.canViewCorrespondents) {
      this.correspondentService
        .listAll()
        .pipe(takeUntil(this.unsubscribeNotifier))
        .subscribe((result) => (this.correspondents = result.results))
    }
    this.classificationService
      .listAll()
      .pipe(takeUntil(this.unsubscribeNotifier))
      .subscribe({
        next: (result) => (this.classifications = result.results),
        // Optional metadata: without access the field is simply not offered.
        error: () => (this.classifications = []),
      })

    this.countRequests
      .pipe(
        debounceTime(400),
        switchMap((scope) => {
          this.counting = true
          this.countFailed = false
          // The correspondence fields have no filter rule type; they go as
          // plain parameters beside the rules.
          const extra = aiSearchScopeToParams({ ...scope, filterRules: [] })
          return this.documentService
            .listFiltered(1, 1, null, null, scope.filterRules ?? [], {
              ...extra,
              fields: 'id',
            })
            .pipe(
              map((result) => result.count),
              catchError(() => {
                this.countFailed = true
                return of(null)
              })
            )
        }),
        takeUntil(this.unsubscribeNotifier)
      )
      .subscribe((count) => {
        this.counting = false
        this.matchCount = count
      })
    this.countRequests.next(this._scope)
  }

  ngOnDestroy(): void {
    this.unsubscribeNotifier.next()
    this.unsubscribeNotifier.complete()
  }

  onRulesChange(rules: FilterRule[]): void {
    this.update({ ...this._scope, filterRules: rules })
  }

  onCorrespondenceChange(): void {
    this.update({ ...this._scope })
  }

  clear(): void {
    this.update(emptyAiSearchScope())
  }

  private update(scope: AiSearchScope): void {
    this._scope = scope
    this.scopeChange.emit(scope)
    this.countRequests.next(scope)
  }
}
