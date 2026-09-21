import { provideHttpClient } from '@angular/common/http'
import { provideHttpClientTesting } from '@angular/common/http/testing'
import { ChangeDetectorRef } from '@angular/core'
import { TestBed } from '@angular/core/testing'
import { Subject } from 'rxjs'
import { SETTINGS_KEYS } from 'src/app/data/ui-settings'
import { AiConversationStoreService } from 'src/app/services/ai-conversation-store.service'
import { AiReportExportService } from 'src/app/services/ai-report-export.service'
import { ChatService } from 'src/app/services/chat.service'
import { SettingsService } from 'src/app/services/settings.service'
import { ToastService } from 'src/app/services/toast.service'
import { AiPageComponent } from './ai-page.component'

describe('AiPageComponent', () => {
  let component: AiPageComponent
  let store: AiConversationStoreService
  let chatService: ChatService
  let exporter: AiReportExportService
  let stream: Subject<string>
  let streamSpy: jest.SpyInstance

  beforeEach(() => {
    localStorage.clear()
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        // Only available inside a real view; stubbed for direct construction.
        {
          provide: ChangeDetectorRef,
          useValue: { markForCheck: jest.fn(), detectChanges: jest.fn() },
        },
        {
          provide: SettingsService,
          useValue: {
            currentUser: { username: 'alice' },
            get: (key: string) => key === SETTINGS_KEYS.AI_ENABLED,
          },
        },
        {
          provide: ToastService,
          useValue: { showError: jest.fn(), showInfo: jest.fn() },
        },
      ],
    })
    store = TestBed.inject(AiConversationStoreService)
    chatService = TestBed.inject(ChatService)
    exporter = TestBed.inject(AiReportExportService)
    stream = new Subject<string>()
    streamSpy = jest.spyOn(chatService, 'streamChat').mockReturnValue(stream)
    // Constructed directly: rendering specs cannot run in this environment.
    component = TestBed.runInInjectionContext(() => new AiPageComponent())
    component.ngOnInit()
  })

  afterEach(() => {
    component.ngOnDestroy()
    localStorage.clear()
    jest.restoreAllMocks()
  })

  it('should send the question in the selected mode and stream the answer', () => {
    component.setMode('deep')
    component.input = '  ما هي العقود؟  '
    component.send()

    expect(streamSpy).toHaveBeenCalledWith(undefined, 'ما هي العقود؟', 'deep')
    expect(component.loading).toBeTruthy()
    expect(component.input).toEqual('')
    expect(component.current.title).toEqual('ما هي العقود؟')

    stream.next('جواب')
    stream.next('جواب كامل')
    const answer = component.current.messages[1]
    expect(answer.content).toEqual('جواب كامل')
    expect(answer.isStreaming).toBeTruthy()

    stream.complete()
    expect(answer.isStreaming).toBeFalsy()
    expect(answer.mode).toEqual('deep')
    expect(component.loading).toBeFalsy()
  })

  it('should save the conversation and list it in the history', () => {
    component.input = 'سؤال'
    component.send()
    stream.next('جواب')
    stream.complete()

    const saved = store.list()
    expect(saved).toHaveLength(1)
    expect(saved[0].messages.map((m) => m.content)).toEqual(['سؤال', 'جواب'])
    expect(component.conversations).toHaveLength(1)
  })

  it('should not send while an answer is arriving or when the input is blank', () => {
    component.input = '   '
    component.send()
    expect(streamSpy).not.toHaveBeenCalled()

    component.input = 'first'
    component.send()
    component.input = 'second'
    component.send()
    expect(streamSpy).toHaveBeenCalledTimes(1)
  })

  it('should keep the partial answer when stopped', () => {
    component.input = 'q'
    component.send()
    stream.next('partial')
    component.stop()

    const answer = component.current.messages[1]
    expect(answer.content).toEqual('partial')
    expect(answer.incomplete).toBeTruthy()
    expect(component.loading).toBeFalsy()
    expect(stream.observed).toBeFalsy()
  })

  it('should show a message instead of an empty answer when the request fails', () => {
    component.input = 'q'
    component.send()
    stream.error(new Error('boom'))

    const answer = component.current.messages[1]
    expect(answer.content).toContain('تعذّر الحصول على رد')
    expect(answer.incomplete).toBeTruthy()
    expect(component.loading).toBeFalsy()
  })

  it('should reject a question over the length limit', () => {
    component.input = 'x'.repeat(4001)
    component.send()
    expect(streamSpy).not.toHaveBeenCalled()
    expect(component.current.messages).toHaveLength(0)
  })

  it('should open a saved conversation as a copy and start a new one', () => {
    component.input = 'q'
    component.send()
    stream.next('a')
    stream.complete()
    const saved = component.conversations[0]

    component.newConversation()
    expect(component.current.messages).toHaveLength(0)
    expect(component.current.id).not.toEqual(saved.id)

    component.openConversation(saved)
    expect(component.current.id).toEqual(saved.id)
    expect(component.current).not.toBe(saved)
  })

  it('should delete a conversation and reset if it was the open one', () => {
    component.input = 'q'
    component.send()
    stream.complete()
    const saved = component.conversations[0]

    component.deleteConversation(saved, new Event('click'))
    expect(component.conversations).toHaveLength(0)
    expect(component.current.id).not.toEqual(saved.id)
  })

  it('should report a full browser storage', () => {
    jest.spyOn(store, 'save').mockReturnValue(false)
    component.input = 'q'
    component.send()
    expect(component.storageOk).toBeFalsy()
  })

  it('should export a single answer paired with its question', () => {
    const pdf = jest.spyOn(exporter, 'downloadPdf').mockResolvedValue()
    component.input = 'السؤال هنا'
    component.send()
    stream.next('الجواب هنا')
    stream.complete()

    component.exportAnswer(component.current.messages[1], 'pdf')
    expect(pdf).toHaveBeenCalledWith(
      expect.objectContaining({
        entries: [
          expect.objectContaining({
            question: 'السؤال هنا',
            answer: 'الجواب هنا',
          }),
        ],
      })
    )
  })

  it('should only allow exporting once there is a finished answer', () => {
    expect(component.canExport).toBeFalsy()
    component.input = 'q'
    component.send()
    stream.next('partial')
    expect(component.canExport).toBeFalsy()
    stream.complete()
    expect(component.canExport).toBeTruthy()
  })

  it('should remember the chosen mode', () => {
    component.setMode('deep')
    expect(store.getMode()).toEqual('deep')
  })

  it('should send on Enter but not on Shift+Enter or while composing', () => {
    component.input = 'q'
    const enter = (init: KeyboardEventInit) =>
      new KeyboardEvent('keydown', { key: 'Enter', cancelable: true, ...init })

    component.onComposerKeydown(enter({ shiftKey: true }))
    component.onComposerKeydown(enter({ isComposing: true }))
    expect(streamSpy).not.toHaveBeenCalled()

    const event = enter({})
    component.onComposerKeydown(event)
    expect(event.defaultPrevented).toBeTruthy()
    expect(streamSpy).toHaveBeenCalledTimes(1)
  })

  it('should format durations', () => {
    expect(component.formatElapsed(undefined)).toEqual('')
    expect(component.formatElapsed(42_000)).toEqual('42 ث')
    expect(component.formatElapsed(125_000)).toEqual('2 د 5 ث')
  })
})
