import { AiConversation } from 'src/app/data/ai-conversation'
import {
  buildReportBlocks,
  conversationToReport,
  isRtlText,
  markdownToBlocks,
  reportToMarkdown,
  safeFileName,
  stripInlineMarkdown,
} from './ai-report-export.service'

describe('AI report export', () => {
  it('should strip inline markdown', () => {
    expect(
      stripInlineMarkdown('**bold** and *it* and `code` and [link](http://x)')
    ).toEqual('bold and it and code and link')
  })

  it('should decide direction from the first strong letter', () => {
    expect(isRtlText('مرحبا بالعالم')).toBeTruthy()
    expect(isRtlText('Hello world')).toBeFalsy()
    expect(isRtlText('مرحبا Hello')).toBeTruthy()
    expect(isRtlText('Hello مرحبا')).toBeFalsy()
    // No letters: follows the Arabic-first report.
    expect(isRtlText('12345 - 67')).toBeTruthy()
  })

  it('should turn markdown into printable blocks', () => {
    const blocks = markdownToBlocks(
      [
        '# عنوان',
        '',
        'فقرة **مهمة**.',
        '',
        '- أول',
        '  - فرعي',
        '- ثاني',
        '',
        '1. واحد',
        '2. اثنان',
        '',
        '```',
        'code line',
        '```',
      ].join('\n')
    )
    expect(blocks.map((b) => b.kind)).toEqual([
      'h1',
      'p',
      'li',
      'li',
      'li',
      'li',
      'li',
      'code',
    ])
    expect(blocks[1].text).toEqual('فقرة مهمة.')
    expect(blocks[3]).toMatchObject({ text: 'فرعي', depth: 1 })
    expect(blocks[5]).toMatchObject({ marker: '1.' })
    expect(blocks[6]).toMatchObject({ marker: '2.' })
  })

  it('should flatten tables into lines', () => {
    const blocks = markdownToBlocks('| a | b |\n|---|---|\n| 1 | 2 |')
    expect(blocks.map((b) => b.text)).toEqual(['a  |  b', '1  |  2'])
  })

  it('should not choke on empty input', () => {
    expect(markdownToBlocks('')).toEqual([])
    expect(markdownToBlocks(undefined as any)).toEqual([])
  })

  const conversation = {
    id: '1',
    title: 'تقرير',
    createdAt: 1,
    updatedAt: 2,
    messages: [
      { role: 'user', content: 'سؤال أول', createdAt: 1 },
      { role: 'assistant', content: 'جواب أول', mode: 'fast', createdAt: 1 },
      { role: 'user', content: 'سؤال ثان', createdAt: 2 },
      {
        role: 'assistant',
        content: 'نصف جواب',
        createdAt: 2,
        isStreaming: true,
      },
      { role: 'user', content: 'سؤال ثالث', createdAt: 3 },
      { role: 'assistant', content: 'جواب ثالث', mode: 'deep', createdAt: 3 },
    ],
  } as AiConversation

  it('should pair questions with answers and skip unfinished ones', () => {
    const report = conversationToReport(conversation, 5)
    expect(report.entries.map((e) => [e.question, e.answer])).toEqual([
      ['سؤال أول', 'جواب أول'],
      ['سؤال ثالث', 'جواب ثالث'],
    ])
  })

  it('should build blocks with labels and separators between entries', () => {
    const blocks = buildReportBlocks(conversationToReport(conversation, 5))
    expect(blocks.filter((b) => b.kind === 'hr')).toHaveLength(1)
    expect(blocks.filter((b) => b.kind === 'label').map((b) => b.text)).toEqual(
      ['السؤال', 'الإجابة — إجابة سريعة', 'السؤال', 'الإجابة — تفكير عميق']
    )
  })

  it('should write markdown with the title, questions and answers', () => {
    const md = reportToMarkdown(conversationToReport(conversation, 5))
    expect(md.startsWith('# تقرير')).toBeTruthy()
    expect(md).toContain('## السؤال\n\nسؤال أول')
    expect(md).toContain('## الإجابة (تفكير عميق)\n\nجواب ثالث')
    expect(md.endsWith('\n')).toBeTruthy()
  })

  it('should build safe file names that keep Arabic letters', () => {
    expect(safeFileName('تقرير: العقود؟ / 2026', 'pdf')).toEqual(
      'تقرير العقود؟ 2026.pdf'
    )
    expect(safeFileName('', 'md')).toEqual('report.md')
    expect(safeFileName('a'.repeat(200), 'md').length).toBeLessThanOrEqual(83)
  })
})
