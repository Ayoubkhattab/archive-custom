import { Injectable } from '@angular/core'
import { jsPDF } from 'jspdf'
import { marked } from 'marked'
import {
  AiConversation,
  AiMode,
  aiModeLabel,
} from 'src/app/data/ai-conversation'

/**
 * Exports an AI answer or a whole conversation as a report.
 *
 * The PDF is composed on offscreen canvases and each page is placed in the file
 * as an image. Text is drawn with the Canvas 2D API, so the browser does the
 * Arabic shaping and bidirectional reordering itself and jsPDF never has to
 * embed or shape an Arabic font, which is where RTL PDF output usually breaks.
 * The trade-off is that the text in the PDF cannot be selected; the Markdown
 * export is there for anyone who needs the text itself.
 */

export interface AiReportEntry {
  question?: string
  answer: string
  mode?: AiMode
}

export interface AiReport {
  title: string
  entries: AiReportEntry[]
  generatedAt: number
}

export type BlockKind =
  | 'label'
  | 'question'
  | 'h1'
  | 'h2'
  | 'h3'
  | 'p'
  | 'li'
  | 'code'
  | 'quote'
  | 'hr'

export interface ReportBlock {
  kind: BlockKind
  text: string
  /** Nesting level of a list item, starting at 0. */
  depth?: number
  /** Marker drawn beside a list item: a bullet or "1.". */
  marker?: string
}

// A4 portrait at 96dpi, in CSS pixels. Pages are rendered at SCALE times this
// for a crisp result and scaled back down when placed in the PDF.
const PAGE_WIDTH = 794
const PAGE_HEIGHT = 1123
const SCALE = 2
const MARGIN = 48
const CONTENT_WIDTH = PAGE_WIDTH - MARGIN * 2
const FOOTER_HEIGHT = 32

const COLOR_FOREST = '#428177'
const COLOR_FOREST_DARK = '#054239'
const COLOR_INK = '#212529'
const COLOR_MUTED = '#6c757d'
const COLOR_RULE = '#dee2e6'
const COLOR_TINT = '#f5f7f6'

const FONT_STACK =
  '"ITF Qomra Arabic", "Segoe UI", "Noto Naskh Arabic", system-ui, sans-serif'
const MONO_STACK = 'Consolas, "Courier New", monospace'

const ARABIC_LETTER = /[֐-ࣿיִ-﷿ﹰ-﻿]/
const LATIN_LETTER = /[A-Za-z]/

/** Drops inline Markdown so the text reads cleanly on a page. */
export function stripInlineMarkdown(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/(\*\*|__)(.+?)\1/g, '$2')
    .replace(/(\*|_)(.+?)\1/g, '$2')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/~~(.+?)~~/g, '$1')
}

/** Whether a run of text should be laid out right-to-left. */
export function isRtlText(text: string): boolean {
  // The first strong letter decides, as in the Unicode bidi algorithm. Text
  // with no letters at all (numbers, symbols) follows the report, which is
  // Arabic-first.
  const arabic = text.search(ARABIC_LETTER)
  const latin = text.search(LATIN_LETTER)
  if (arabic === -1 && latin === -1) return true
  if (arabic === -1) return false
  if (latin === -1) return true
  return arabic < latin
}

/** Flattens Markdown into the handful of block types the PDF knows how to draw. */
export function markdownToBlocks(markdown: string): ReportBlock[] {
  const blocks: ReportBlock[] = []
  const tokens = marked.lexer(markdown ?? '') as any[]

  const addList = (list: any, depth: number) => {
    list.items.forEach((item: any, index: number) => {
      const marker = list.ordered ? `${(list.start || 1) + index}.` : '•'
      const own = (item.tokens ?? []).filter((t: any) => t.type !== 'list')
      const text = own
        .map((t: any) => t.text ?? t.raw ?? '')
        .join(' ')
        .trim()
      if (text) {
        blocks.push({
          kind: 'li',
          text: stripInlineMarkdown(text),
          depth,
          marker,
        })
      }
      ;(item.tokens ?? [])
        .filter((t: any) => t.type === 'list')
        .forEach((nested: any) => addList(nested, depth + 1))
    })
  }

  for (const token of tokens) {
    switch (token.type) {
      case 'heading':
        blocks.push({
          kind: token.depth <= 1 ? 'h1' : token.depth === 2 ? 'h2' : 'h3',
          text: stripInlineMarkdown(token.text),
        })
        break
      case 'paragraph':
      case 'text':
        blocks.push({ kind: 'p', text: stripInlineMarkdown(token.text) })
        break
      case 'list':
        addList(token, 0)
        break
      case 'code':
        blocks.push({ kind: 'code', text: token.text })
        break
      case 'blockquote':
        blocks.push({
          kind: 'quote',
          text: stripInlineMarkdown(token.text ?? '').replace(/^>\s?/gm, ''),
        })
        break
      case 'hr':
        blocks.push({ kind: 'hr', text: '' })
        break
      case 'table': {
        // Columns cannot be laid out reliably across scripts, so each row is
        // written as one line with the cells separated.
        const cells = (row: any[]) =>
          row.map((c: any) => stripInlineMarkdown(c.text)).join('  |  ')
        blocks.push({ kind: 'p', text: cells(token.header) })
        token.rows.forEach((row: any[]) =>
          blocks.push({ kind: 'li', text: cells(row), depth: 0, marker: '•' })
        )
        break
      }
      default:
        break // space, html and definitions carry nothing to print
    }
  }
  return blocks.filter((b) => b.kind === 'hr' || b.text.trim().length > 0)
}

export function buildReportBlocks(report: AiReport): ReportBlock[] {
  const blocks: ReportBlock[] = []
  report.entries.forEach((entry, index) => {
    if (entry.question) {
      blocks.push({ kind: 'label', text: 'السؤال' })
      blocks.push({ kind: 'question', text: entry.question })
    }
    const mode = entry.mode ? ` — ${aiModeLabel(entry.mode)}` : ''
    blocks.push({ kind: 'label', text: `الإجابة${mode}` })
    blocks.push(...markdownToBlocks(entry.answer))
    if (index < report.entries.length - 1) blocks.push({ kind: 'hr', text: '' })
  })
  return blocks
}

/** Pairs each question with the answer that follows it. */
export function conversationToReport(
  conversation: AiConversation,
  generatedAt = Date.now()
): AiReport {
  const entries: AiReportEntry[] = []
  let pendingQuestion: string | undefined
  for (const message of conversation.messages) {
    if (message.role === 'user') {
      pendingQuestion = message.content
    } else if (message.content.trim() && !message.isStreaming) {
      entries.push({
        question: pendingQuestion,
        answer: message.content,
        mode: message.mode,
      })
      pendingQuestion = undefined
    }
  }
  return { title: conversation.title, entries, generatedAt }
}

export function reportToMarkdown(report: AiReport): string {
  const date = new Date(report.generatedAt).toLocaleString('ar')
  const parts = [`# ${report.title}`, `*${date}*`]
  for (const entry of report.entries) {
    if (entry.question) {
      parts.push(`## السؤال\n\n${entry.question}`)
    }
    const mode = entry.mode ? ` (${aiModeLabel(entry.mode)})` : ''
    parts.push(`## الإجابة${mode}\n\n${entry.answer}`)
  }
  return parts.join('\n\n---\n\n') + '\n'
}

/** A file name safe on every platform, keeping Arabic letters intact. */
export function safeFileName(title: string, extension: string): string {
  const base =
    (title || 'report')
      .replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 80) || 'report'
  return `${base}.${extension}`
}

function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Revoking straight away can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

@Injectable({
  providedIn: 'root',
})
export class AiReportExportService {
  downloadMarkdown(report: AiReport): void {
    downloadBlob(
      new Blob([reportToMarkdown(report)], {
        type: 'text/markdown;charset=utf-8',
      }),
      safeFileName(report.title, 'md')
    )
  }

  async downloadPdf(report: AiReport): Promise<void> {
    await this.ensureFontsReady()

    const pages: HTMLCanvasElement[] = []
    const renderer = new PageRenderer(report, pages)
    renderer.render(buildReportBlocks(report))

    const doc = new jsPDF({
      orientation: 'portrait',
      unit: 'px',
      format: [PAGE_WIDTH, PAGE_HEIGHT],
      compress: true,
    })
    pages.forEach((page, index) => {
      if (index > 0) doc.addPage([PAGE_WIDTH, PAGE_HEIGHT], 'portrait')
      doc.addImage(
        page.toDataURL('image/jpeg', 0.92),
        'JPEG',
        0,
        0,
        PAGE_WIDTH,
        PAGE_HEIGHT
      )
    })
    doc.save(safeFileName(report.title, 'pdf'))
  }

  private async ensureFontsReady(): Promise<void> {
    // Without this the first page can be measured with a fallback font, which
    // changes every text width and misplaces the line breaks.
    try {
      const fonts = (document as any).fonts
      await fonts?.load?.('16px "ITF Qomra Arabic"')
      await fonts?.load?.('700 16px "ITF Qomra Arabic"')
      await fonts?.ready
    } catch {
      // Font loading API unavailable; system fonts will be used.
    }
  }
}

interface Style {
  font: string
  color: string
  lineHeight: number
  /** Space above the block. */
  before: number
  /** Space below the block. */
  after: number
}

const STYLES: Record<Exclude<BlockKind, 'hr'>, Style> = {
  label: {
    font: `700 13px ${FONT_STACK}`,
    color: COLOR_FOREST,
    lineHeight: 20,
    before: 14,
    after: 4,
  },
  question: {
    font: `600 14px ${FONT_STACK}`,
    color: COLOR_INK,
    lineHeight: 24,
    before: 0,
    after: 6,
  },
  h1: {
    font: `700 20px ${FONT_STACK}`,
    color: COLOR_FOREST_DARK,
    lineHeight: 30,
    before: 14,
    after: 6,
  },
  h2: {
    font: `700 17px ${FONT_STACK}`,
    color: COLOR_FOREST_DARK,
    lineHeight: 27,
    before: 12,
    after: 4,
  },
  h3: {
    font: `700 15px ${FONT_STACK}`,
    color: COLOR_INK,
    lineHeight: 25,
    before: 10,
    after: 2,
  },
  p: {
    font: `400 14px ${FONT_STACK}`,
    color: COLOR_INK,
    lineHeight: 25,
    before: 0,
    after: 8,
  },
  li: {
    font: `400 14px ${FONT_STACK}`,
    color: COLOR_INK,
    lineHeight: 25,
    before: 0,
    after: 3,
  },
  code: {
    font: `400 12px ${MONO_STACK}`,
    color: COLOR_INK,
    lineHeight: 18,
    before: 4,
    after: 10,
  },
  quote: {
    font: `italic 400 14px ${FONT_STACK}`,
    color: COLOR_MUTED,
    lineHeight: 24,
    before: 0,
    after: 8,
  },
}

/**
 * Draws report blocks onto canvases, starting a new page when one is full.
 *
 * Each block picks its own direction, so an English term or a code sample inside
 * an Arabic report is aligned to its own start edge instead of being dragged to
 * the right.
 */
class PageRenderer {
  private canvas!: HTMLCanvasElement
  private ctx!: CanvasRenderingContext2D
  private y = 0

  constructor(
    private report: AiReport,
    private pages: HTMLCanvasElement[]
  ) {}

  render(blocks: ReportBlock[]): void {
    this.startPage()
    this.drawTitle()
    blocks.forEach((block) => this.drawBlock(block))
    this.stampFooters()
  }

  private startPage(): void {
    this.canvas = document.createElement('canvas')
    this.canvas.width = PAGE_WIDTH * SCALE
    this.canvas.height = PAGE_HEIGHT * SCALE
    this.ctx = this.canvas.getContext('2d')!
    this.ctx.scale(SCALE, SCALE)
    this.ctx.textBaseline = 'alphabetic'
    this.ctx.fillStyle = '#ffffff'
    this.ctx.fillRect(0, 0, PAGE_WIDTH, PAGE_HEIGHT)
    this.pages.push(this.canvas)
    this.y = MARGIN
  }

  private get remaining(): number {
    return PAGE_HEIGHT - MARGIN - FOOTER_HEIGHT - this.y
  }

  private ensureSpace(height: number): void {
    if (this.remaining < height) this.startPage()
  }

  private drawTitle(): void {
    const ctx = this.ctx
    const rtl = isRtlText(this.report.title)
    this.setDirection(rtl)
    ctx.fillStyle = COLOR_FOREST_DARK
    ctx.font = `700 24px ${FONT_STACK}`
    const lines = this.wrap(this.report.title, CONTENT_WIDTH)
    lines.forEach((line) => {
      this.y += 34
      this.fillText(line, rtl, 0)
    })

    ctx.fillStyle = COLOR_MUTED
    ctx.font = `400 12px ${FONT_STACK}`
    this.y += 22
    this.setDirection(true)
    this.fillText(
      new Date(this.report.generatedAt).toLocaleString('ar'),
      true,
      0
    )

    this.y += 12
    this.rule(COLOR_FOREST, 2)
    this.y += 10
  }

  private drawBlock(block: ReportBlock): void {
    if (block.kind === 'hr') {
      this.ensureSpace(24)
      this.y += 10
      this.rule(COLOR_RULE, 1)
      this.y += 14
      return
    }

    const style = STYLES[block.kind]
    const ctx = this.ctx
    const rtl = isRtlText(block.text)
    const indent =
      block.kind === 'li'
        ? 18 + (block.depth ?? 0) * 18
        : block.kind === 'quote'
          ? 14
          : 0
    const width = CONTENT_WIDTH - indent - (block.kind === 'code' ? 16 : 0)

    ctx.font = style.font
    this.setDirection(rtl)
    const lines =
      block.kind === 'code'
        ? block.text.split('\n').flatMap((l) => this.wrap(l, width))
        : this.wrap(block.text, width)

    this.y += style.before
    lines.forEach((line, index) => {
      // Keep a heading with the line that follows it, and never leave a
      // label alone at the bottom of a page.
      const needed =
        style.lineHeight +
        (index === 0 && /^(label|h1|h2|h3)$/.test(block.kind) ? 40 : 0)
      this.ensureSpace(needed)
      // A page break resets the context state.
      ctx.font = style.font
      this.setDirection(rtl)

      if (block.kind === 'code') {
        ctx.fillStyle = COLOR_TINT
        ctx.fillRect(MARGIN, this.y, CONTENT_WIDTH, style.lineHeight)
      }
      if (block.kind === 'quote' && index === 0) {
        ctx.fillStyle = COLOR_FOREST
        const barX = rtl ? PAGE_WIDTH - MARGIN - 3 : MARGIN
        ctx.fillRect(barX, this.y, 3, lines.length * style.lineHeight)
      }

      this.y += style.lineHeight
      ctx.fillStyle = style.color
      const inset = block.kind === 'code' ? 8 : 0
      this.fillText(line, rtl, indent + inset, this.y - 7)

      if (block.kind === 'li' && index === 0 && block.marker) {
        this.fillText(block.marker, rtl, indent - 16, this.y - 7)
      }
    })
    this.y += style.after
  }

  /** Stamped last, because "page 2 of 3" needs the final page count. */
  private stampFooters(): void {
    this.pages.forEach((page, index) => {
      const ctx = page.getContext('2d')!
      ctx.save()
      ctx.scale(SCALE, SCALE)
      const top = PAGE_HEIGHT - MARGIN - FOOTER_HEIGHT + 8
      ctx.strokeStyle = COLOR_RULE
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(MARGIN, top)
      ctx.lineTo(PAGE_WIDTH - MARGIN, top)
      ctx.stroke()

      ctx.fillStyle = COLOR_MUTED
      ctx.font = `400 11px ${FONT_STACK}`
      ctx.direction = 'rtl'
      ctx.textAlign = 'right'
      ctx.fillText('أرشيف المعلومات', PAGE_WIDTH - MARGIN, top + 18)
      ctx.textAlign = 'left'
      ctx.fillText(
        `صفحة ${index + 1} من ${this.pages.length}`,
        MARGIN,
        top + 18
      )
      ctx.restore()
    })
  }

  private setDirection(rtl: boolean): void {
    this.ctx.direction = rtl ? 'rtl' : 'ltr'
    this.ctx.textAlign = rtl ? 'right' : 'left'
  }

  /** Draws at the block's own start edge, `inset` pixels in. */
  private fillText(
    text: string,
    rtl: boolean,
    inset: number,
    y = this.y
  ): void {
    const x = rtl ? PAGE_WIDTH - MARGIN - inset : MARGIN + inset
    this.ctx.fillText(text, x, y)
  }

  private rule(color: string, thickness: number): void {
    this.ctx.strokeStyle = color
    this.ctx.lineWidth = thickness
    this.ctx.beginPath()
    this.ctx.moveTo(MARGIN, this.y)
    this.ctx.lineTo(PAGE_WIDTH - MARGIN, this.y)
    this.ctx.stroke()
  }

  /** Breaks text into lines no wider than `maxWidth` in the current font. */
  private wrap(text: string, maxWidth: number): string[] {
    const lines: string[] = []
    for (const paragraph of text.split('\n')) {
      let line = ''
      for (const word of paragraph.split(/\s+/).filter(Boolean)) {
        const candidate = line ? `${line} ${word}` : word
        if (this.ctx.measureText(candidate).width <= maxWidth) {
          line = candidate
          continue
        }
        if (line) lines.push(line)
        line = word
        // A single word wider than the column (a long URL) is cut by character.
        while (this.ctx.measureText(line).width > maxWidth && line.length > 1) {
          let cut = line.length - 1
          while (
            cut > 1 &&
            this.ctx.measureText(line.slice(0, cut)).width > maxWidth
          ) {
            cut--
          }
          lines.push(line.slice(0, cut))
          line = line.slice(cut)
        }
      }
      lines.push(line)
    }
    return lines
  }
}
