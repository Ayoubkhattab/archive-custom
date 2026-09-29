import { TestBed } from '@angular/core/testing'
import { Observable, firstValueFrom } from 'rxjs'
import { DialogService } from '../services/dialog.service'
import { ComponentCanDeactivate, DirtyDocGuard } from './dirty-doc.guard'

describe('DirtyDocGuard', () => {
  let guard: DirtyDocGuard
  let dialogService: { confirm: jest.Mock }
  const component: ComponentCanDeactivate = { canDeactivate: () => true }

  beforeEach(() => {
    dialogService = { confirm: jest.fn() }
    TestBed.configureTestingModule({
      providers: [
        DirtyDocGuard,
        { provide: DialogService, useValue: dialogService },
      ],
    })
    guard = TestBed.inject(DirtyDocGuard)
  })

  it('should deactivate if component is not dirty', () => {
    component.canDeactivate = () => true
    expect(guard.canDeactivate(component)).toBe(true)
    expect(dialogService.confirm).not.toHaveBeenCalled()
  })

  it('should ask before leaving a dirty component', async () => {
    component.canDeactivate = () => false
    dialogService.confirm.mockResolvedValue(false)
    const result = guard.canDeactivate(component) as Observable<boolean>
    expect(dialogService.confirm).toHaveBeenCalled()
    expect(await firstValueFrom(result)).toBe(false)
  })

  it('should leave when the person confirms', async () => {
    component.canDeactivate = () => false
    dialogService.confirm.mockResolvedValue(true)
    const result = guard.canDeactivate(component) as Observable<boolean>
    expect(await firstValueFrom(result)).toBe(true)
  })
})
