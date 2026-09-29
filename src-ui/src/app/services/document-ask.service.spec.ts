import { TestBed } from '@angular/core/testing'
import { Subject, of, throwError } from 'rxjs'
import { Document } from '../data/document'
import { ChatService, DocumentAskChunk } from './chat.service'
import { DocumentAskService } from './document-ask.service'
import { DocumentService } from './rest/document.service'

describe('DocumentAskService', () => {
  let service: DocumentAskService
  let chat: { askDocument: jest.Mock; warmDocument: jest.Mock }
  const doc = {
    id: 5,
    title: 'عقد',
    content: 'مدة الضمان ثلاث سنوات من تاريخ التسليم',
  } as Document

  beforeEach(() => {
    chat = { askDocument: jest.fn(), warmDocument: jest.fn(() => of(null)) }
    TestBed.configureTestingModule({
      providers: [
        DocumentAskService,
        { provide: ChatService, useValue: chat },
        { provide: DocumentService, useValue: { get: () => of(doc) } },
      ],
    })
    service = TestBed.inject(DocumentAskService)
  })

  it('streams an answer and checks its quotes', () => {
    const stream = new Subject<DocumentAskChunk>()
    chat.askDocument.mockReturnValue(stream)
    service.open(doc)
    service.ask('ما مدة الضمان؟')

    expect(service.busy).toBe(true)
    stream.next({ context: 'complete' })
    stream.next({ text: '​' }) // heartbeat only: still waiting
    expect(service.conversation.messages[1].state).toEqual('waiting')
    stream.next({ text: '​ثلاث سنوات.\nالمصدر: «مدة الضمان ثلاث سنوات»' })
    stream.complete()

    const answer = service.conversation.messages[1]
    expect(answer.state).toEqual('done')
    expect(answer.context).toEqual('complete')
    expect(answer.parsed.body).toEqual('ثلاث سنوات.')
    expect(answer.quotes).toEqual([
      { text: 'مدة الضمان ثلاث سنوات', status: 'exact' },
    ])
    expect(answer.firstWordMs).toBeDefined()
    expect(service.busy).toBe(false)
  })

  it('sends only finished turns as history', () => {
    chat.askDocument.mockReturnValueOnce(of({ text: 'جواب 1' }))
    service.open(doc)
    service.ask('سؤال 1')
    chat.askDocument.mockReturnValueOnce(throwError(() => ({ status: 504 })))
    service.ask('سؤال 2')
    chat.askDocument.mockReturnValueOnce(of({ text: 'جواب 3' }))
    service.ask('سؤال 3')

    const history = chat.askDocument.mock.calls[2][2]
    expect(history).toEqual([
      { role: 'user', content: 'سؤال 1' },
      { role: 'assistant', content: 'جواب 1' },
    ])
  })

  it('keeps a separate conversation per document', () => {
    chat.askDocument.mockReturnValue(of({ text: 'جواب' }))
    service.open(doc)
    service.ask('سؤال')
    service.open({ ...doc, id: 6 } as Document)
    expect(service.conversation.messages).toEqual([])
    service.open(doc)
    expect(service.conversation.messages.length).toEqual(2)
  })

  it('explains a document without text', () => {
    chat.askDocument.mockReturnValue(
      throwError(() => ({
        status: 400,
        error: 'This document has no extracted text yet.',
      }))
    )
    service.open(doc)
    service.ask('سؤال')
    const answer = service.conversation.messages[1]
    expect(answer.state).toEqual('error')
    expect(answer.content).toContain('extracted text')
  })

  it('stops a running answer and keeps what arrived', () => {
    const stream = new Subject<DocumentAskChunk>()
    chat.askDocument.mockReturnValue(stream)
    service.open(doc)
    service.ask('سؤال')
    stream.next({ text: 'جزء من الجواب' })
    service.stop()
    const answer = service.conversation.messages[1]
    expect(answer.state).toEqual('stopped')
    expect(answer.content).toEqual('جزء من الجواب')
    expect(service.busy).toBe(false)
  })

  it('pre-reads the document when opened, only before the first question', () => {
    chat.askDocument.mockReturnValue(of({ text: 'جواب' }))
    service.open(doc)
    expect(chat.warmDocument).toHaveBeenCalledWith(5)
    service.ask('سؤال')
    service.close()
    service.open(doc)
    expect(chat.warmDocument).toHaveBeenCalledTimes(1)
  })
})
