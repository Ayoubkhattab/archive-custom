import {
  QuoteStatus,
  checkQuote,
  locateQuote,
  parseAnswer,
} from './answer-sources'

const DOCUMENT = `وزارة الصحة
عقد توريد رقم ٤٥/٢٠٢٦
مدة الضمان ثلاث سنوات من تاريخ
التسليم النهائي للأجهزة الطبية.
قيمة العقد: 125,000 دينار`

describe('parseAnswer', () => {
  it('splits the source line from the answer', () => {
    const parsed = parseAnswer(
      'مدة الضمان ثلاث سنوات.\nالمصدر: «مدة الضمان ثلاث سنوات من تاريخ التسليم»'
    )
    expect(parsed.body).toEqual('مدة الضمان ثلاث سنوات.')
    expect(parsed.quotes).toEqual(['مدة الضمان ثلاث سنوات من تاريخ التسليم'])
    expect(parsed.notInDocument).toBe(false)
  })

  it('accepts a bolded label, several quotes and other quote marks', () => {
    const parsed = parseAnswer(
      'الجواب\n\n**المصدر:** «أولاً» و “ثانياً هنا” و "ثالثاً"'
    )
    expect(parsed.body).toEqual('الجواب')
    expect(parsed.quotes).toEqual(['أولاً', 'ثانياً هنا', 'ثالثاً'])
  })

  it('uses the last source line only', () => {
    const parsed = parseAnswer(
      'المصدر: في النص أدناه\nجواب\nالمصدر: «النص الأول»« النص الثاني»'
    )
    expect(parsed.body).toContain('جواب')
    expect(parsed.quotes).toEqual(['النص الأول', 'النص الثاني'])
  })

  it('recognises an answer the document does not contain', () => {
    const parsed = parseAnswer('لم يرد ذلك في المستند.')
    expect(parsed.notInDocument).toBe(true)
    expect(parsed.quotes).toEqual([])
    expect(parsed.body).toEqual('لم يرد ذلك في المستند.')
  })

  it('handles an answer without a source line', () => {
    const parsed = parseAnswer('جواب بلا مصدر')
    expect(parsed.body).toEqual('جواب بلا مصدر')
    expect(parsed.quotes).toEqual([])
  })
})

describe('checkQuote', () => {
  it('finds a quote across OCR line breaks, digits and letter variants', () => {
    expect(
      checkQuote('مدة الضمان ثلاث سنوات من تاريخ التسليم النهائي', DOCUMENT)
    ).toEqual(QuoteStatus.Exact)
    expect(checkQuote('عقد توريد رقم 45/2026', DOCUMENT)).toEqual(
      QuoteStatus.Exact
    )
    expect(checkQuote('وزاره الصحه', DOCUMENT)).toEqual(QuoteStatus.Exact)
  })

  it('reports a reordered quote as a close match', () => {
    expect(
      checkQuote('ثلاث سنوات مدة الضمان من تاريخ التسليم', DOCUMENT)
    ).toEqual(QuoteStatus.Partial)
  })

  it('reports an invented quote as missing', () => {
    expect(checkQuote('يلتزم المورد بصيانة شهرية مجانية', DOCUMENT)).toEqual(
      QuoteStatus.Missing
    )
    expect(checkQuote('', DOCUMENT)).toEqual(QuoteStatus.Missing)
  })
})

describe('locateQuote', () => {
  it('finds the raw position across a line break', () => {
    const range = locateQuote('ثلاث سنوات من تاريخ التسليم', DOCUMENT)
    expect(range).not.toBeNull()
    expect(DOCUMENT.slice(range.start, range.end)).toEqual(
      'ثلاث سنوات من تاريخ\nالتسليم'
    )
  })

  it('returns null when the words are not there', () => {
    expect(locateQuote('نص غير موجود', DOCUMENT)).toBeNull()
  })
})
