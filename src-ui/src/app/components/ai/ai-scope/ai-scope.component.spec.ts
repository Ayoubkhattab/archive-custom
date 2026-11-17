import { provideHttpClient } from '@angular/common/http'
import { provideHttpClientTesting } from '@angular/common/http/testing'
import { fakeAsync, TestBed, tick } from '@angular/core/testing'
import { of, throwError } from 'rxjs'
import { FILTER_HAS_DOCUMENT_TYPE_ANY } from 'src/app/data/filter-rule-type'
import { PermissionsService } from 'src/app/services/permissions.service'
import { CorrespondentService } from 'src/app/services/rest/correspondent.service'
import { DocumentClassificationService } from 'src/app/services/rest/document-classification.service'
import { DocumentService } from 'src/app/services/rest/document.service'
import { emptyAiSearchScope } from 'src/app/utils/ai-search-scope'
import { AiScopeComponent } from './ai-scope.component'

describe('AiScopeComponent', () => {
  let component: AiScopeComponent
  let listFiltered: jest.Mock

  function create(canViewCorrespondents = true) {
    listFiltered = jest.fn().mockReturnValue(of({ count: 12, results: [] }))
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: DocumentService, useValue: { listFiltered } },
        {
          provide: CorrespondentService,
          useValue: {
            listAll: () => of({ results: [{ id: 1, name: 'الوزارة' }] }),
          },
        },
        {
          provide: DocumentClassificationService,
          useValue: { listAll: () => throwError(() => new Error('403')) },
        },
        {
          provide: PermissionsService,
          useValue: { currentUserCan: () => canViewCorrespondents },
        },
      ],
    })
    // Constructed directly: rendering specs cannot run in this environment.
    component = TestBed.runInInjectionContext(() => new AiScopeComponent())
    component.ngOnInit()
  }

  afterEach(() => component?.ngOnDestroy())

  it('should load the correspondence choices it may show', () => {
    create()
    expect(component.canViewCorrespondents).toBeTruthy()
    expect(component.correspondents.length).toEqual(1)
    // No access to classifications: the field is simply not offered.
    expect(component.classifications).toEqual([])
  })

  it('should skip correspondents without permission', () => {
    create(false)
    expect(component.correspondents).toEqual([])
  })

  it('should count the documents the filters leave', fakeAsync(() => {
    create()
    tick(400)
    expect(component.matchCount).toEqual(12)

    const emitted = jest.fn()
    component.scopeChange.subscribe(emitted)
    const rules = [{ rule_type: FILTER_HAS_DOCUMENT_TYPE_ANY, value: '3' }]
    component.onRulesChange(rules)
    component.scope.senders = [1]
    component.onCorrespondenceChange()
    expect(component.counting).toBeFalsy()
    tick(400)

    // Debounced: one count for the burst of changes.
    expect(listFiltered).toHaveBeenCalledTimes(2)
    expect(listFiltered).toHaveBeenLastCalledWith(1, 1, null, null, rules, {
      sender__id__in: '1',
      fields: 'id',
    })
    expect(emitted).toHaveBeenLastCalledWith(
      expect.objectContaining({ filterRules: rules, senders: [1] })
    )
    expect(component.isEmpty).toBeFalsy()
  }))

  it('should report a failed count instead of a wrong number', fakeAsync(() => {
    create()
    listFiltered.mockReturnValue(throwError(() => new Error('500')))
    component.onRulesChange([
      { rule_type: FILTER_HAS_DOCUMENT_TYPE_ANY, value: '3' },
    ])
    tick(400)
    expect(component.countFailed).toBeTruthy()
    expect(component.matchCount).toBeNull()
  }))

  it('should clear every filter', () => {
    create()
    const emitted = jest.fn()
    component.scopeChange.subscribe(emitted)
    component.onRulesChange([
      { rule_type: FILTER_HAS_DOCUMENT_TYPE_ANY, value: '3' },
    ])
    component.clear()
    expect(emitted).toHaveBeenLastCalledWith(emptyAiSearchScope())
    expect(component.isEmpty).toBeTruthy()
  })
})
