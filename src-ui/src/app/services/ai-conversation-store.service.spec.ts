import { TestBed } from '@angular/core/testing'
import { AiConversation } from 'src/app/data/ai-conversation'
import {
  AiConversationStoreService,
  MAX_CONVERSATIONS,
} from './ai-conversation-store.service'
import { SettingsService } from './settings.service'

function conversation(id: string, updatedAt: number, content = 'hi') {
  return {
    id,
    title: `title ${id}`,
    createdAt: updatedAt,
    updatedAt,
    messages: [{ role: 'user', content, createdAt: updatedAt }],
  } as AiConversation
}

describe('AiConversationStoreService', () => {
  let store: AiConversationStoreService
  let settings: { currentUser: { username: string } }

  beforeEach(() => {
    localStorage.clear()
    settings = { currentUser: { username: 'alice' } }
    TestBed.configureTestingModule({
      providers: [{ provide: SettingsService, useValue: settings }],
    })
    store = TestBed.inject(AiConversationStoreService)
  })

  afterEach(() => {
    jest.restoreAllMocks()
    localStorage.clear()
  })

  it('should save and list conversations, newest first', () => {
    store.save(conversation('a', 1))
    store.save(conversation('b', 3))
    store.save(conversation('c', 2))
    expect(store.list().map((c) => c.id)).toEqual(['b', 'c', 'a'])
  })

  it('should replace a conversation saved again instead of duplicating it', () => {
    store.save(conversation('a', 1, 'first'))
    store.save(conversation('a', 2, 'second'))
    const all = store.list()
    expect(all).toHaveLength(1)
    expect(all[0].messages[0].content).toEqual('second')
  })

  it('should keep each user history separate', () => {
    store.save(conversation('a', 1))
    settings.currentUser = { username: 'bob' }
    expect(store.list()).toEqual([])
    store.save(conversation('b', 1))
    settings.currentUser = { username: 'alice' }
    expect(store.list().map((c) => c.id)).toEqual(['a'])
  })

  it('should never persist an answer that is still streaming', () => {
    const c = conversation('a', 1)
    c.messages.push(
      { role: 'assistant', content: '', createdAt: 1, isStreaming: true },
      { role: 'assistant', content: 'partial', createdAt: 1, isStreaming: true }
    )
    store.save(c)
    const saved = store.list()[0].messages
    // The empty placeholder is dropped; the partial one is kept but marked.
    expect(saved).toHaveLength(2)
    expect(saved[1].content).toEqual('partial')
    expect(saved[1].incomplete).toBeTruthy()
    expect(saved[1].isStreaming).toBeUndefined()
  })

  it('should cap the number of conversations, dropping the oldest', () => {
    for (let i = 0; i < MAX_CONVERSATIONS + 5; i++) {
      store.save(conversation(`c${i}`, i))
    }
    const all = store.list()
    expect(all).toHaveLength(MAX_CONVERSATIONS)
    expect(all.find((c) => c.id === 'c0')).toBeUndefined()
    expect(all[0].id).toEqual(`c${MAX_CONVERSATIONS + 4}`)
  })

  it('should drop old conversations when the quota is full, keeping the new one', () => {
    store.save(conversation('old1', 1))
    store.save(conversation('old2', 2))
    const realSetItem = localStorage.setItem.bind(localStorage)
    jest.spyOn(localStorage, 'setItem').mockImplementation((key, value) => {
      // Only a payload holding a single conversation fits.
      if (JSON.parse(value).length > 1) throw new Error('QuotaExceededError')
      realSetItem(key, value)
    })
    expect(store.save(conversation('new', 3))).toBeTruthy()
    expect(store.list().map((c) => c.id)).toEqual(['new'])
  })

  it('should report failure instead of throwing when storage is unavailable', () => {
    jest.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    jest.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(store.save(conversation('a', 1))).toBeFalsy()
    expect(store.list()).toEqual([])
    expect(store.getMode()).toEqual('fast')
    expect(() => store.setMode('deep')).not.toThrow()
  })

  it('should ignore corrupt storage', () => {
    localStorage.setItem('pngx-ai-conversations:alice', '{not json')
    expect(store.list()).toEqual([])
    localStorage.setItem(
      'pngx-ai-conversations:alice',
      JSON.stringify([{ id: 1 }, conversation('ok', 1), 'x'])
    )
    expect(store.list().map((c) => c.id)).toEqual(['ok'])
  })

  it('should delete one conversation and clear all', () => {
    store.save(conversation('a', 1))
    store.save(conversation('b', 2))
    store.delete('a')
    expect(store.list().map((c) => c.id)).toEqual(['b'])
    store.clear()
    expect(store.list()).toEqual([])
  })

  it('should remember the answer mode and reject unknown values', () => {
    expect(store.getMode()).toEqual('fast')
    store.setMode('deep')
    expect(store.getMode()).toEqual('deep')
    localStorage.setItem('pngx-ai-mode:alice', 'bogus')
    expect(store.getMode()).toEqual('fast')
  })
})
