import { TestBed } from '@angular/core/testing'
import { RecentDocumentsService } from './recent-documents.service'
import { SettingsService } from './settings.service'

describe('RecentDocumentsService', () => {
  let service: RecentDocumentsService
  const settings = { currentUser: { id: 7 } }

  beforeEach(() => {
    localStorage.clear()
    TestBed.configureTestingModule({
      providers: [
        RecentDocumentsService,
        { provide: SettingsService, useValue: settings },
      ],
    })
    service = TestBed.inject(RecentDocumentsService)
  })

  it('records documents most recent first without duplicates', () => {
    service.record({ id: 1, title: 'a', created: null })
    service.record({ id: 2, title: 'b', created: null })
    service.record({ id: 1, title: 'a2', created: null })
    expect(service.list().map((d) => d.id)).toEqual([1, 2])
    expect(service.list()[0].title).toEqual('a2')
  })

  it('caps the list length', () => {
    for (let i = 1; i <= 30; i++) {
      service.record({ id: i, title: `${i}`, created: null })
    }
    expect(service.list().length).toEqual(20)
    expect(service.list()[0].id).toEqual(30)
  })

  it('persists per user', () => {
    service.record({ id: 3, title: 'c', created: null })
    expect(localStorage.getItem('pngx-recent-documents-7')).toContain('"id":3')
  })

  it('removes and clears', () => {
    service.record({ id: 1, title: 'a', created: null })
    service.record({ id: 2, title: 'b', created: null })
    service.remove(1)
    expect(service.list().map((d) => d.id)).toEqual([2])
    service.clear()
    expect(service.list()).toEqual([])
  })

  it('survives corrupt storage', () => {
    localStorage.setItem('pngx-recent-documents-7', '{not json')
    expect(service.list()).toEqual([])
  })
})
