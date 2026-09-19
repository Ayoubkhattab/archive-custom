import { Injectable } from '@angular/core'
import { jsPDF } from 'jspdf'
import {
  CorrespondenceAnalytics,
  EntityAnalytics,
} from 'src/app/data/correspondence-analytics'

/**
 * Builds the printable correspondence report.
 *
 * Every page is composed on an offscreen canvas and then placed into the PDF as
 * a single image. Text is drawn with the Canvas 2D API, so the browser performs
 * Arabic shaping and bidirectional reordering itself — jsPDF never has to embed
 * or shape an Arabic font, which is where RTL PDF output usually breaks.
 */

/** A chart to include, paired with the already-rendered Chart.js canvas. */
export interface ReportChart {
  title: string
  canvas: HTMLCanvasElement
}

export interface ReportMeta {
  title: string
  subtitle?: string
  /** Human-readable description of the reporting period. */
  periodLabel: string
  /** Locale used for number and date formatting. */
  locale: string
  fileName: string
}

interface TableColumn {
  header: string
  /** Fraction of the table width, must sum to 1 across all columns. */
  width: number
  value: (row: EntityAnalytics) => string
  /** Right-aligned within the RTL flow; numeric columns read better centered. */
  numeric?: boolean
}

// A4 portrait at 96dpi, in CSS pixels. The canvas is rendered at SCALE times
// this for a crisp result, then scaled back down when placed in the PDF.
const PAGE_WIDTH = 794
const PAGE_HEIGHT = 1123
const SCALE = 2

const MARGIN = 48
const CONTENT_WIDTH = PAGE_WIDTH - MARGIN * 2

// Brand palette, matching the dashboard widgets.
const COLOR_FOREST = '#428177'
const COLOR_FOREST_DARK = '#054239'
const COLOR_GOLD = '#988561'
const COLOR_UMBER = '#6b1f2a'
const COLOR_INK = '#212529'
const COLOR_MUTED = '#6c757d'
const COLOR_RULE = '#dee2e6'
const COLOR_ZEBRA = '#f5f7f6'

const FONT_STACK =
  '"ITF Qomra Arabic", "Segoe UI", "Noto Naskh Arabic", system-ui, sans-serif'

@Injectable({
  providedIn: 'root',
})
export class CorrespondenceReportPdfService {
  async export(
    report: CorrespondenceAnalytics,
    charts: ReportChart[],
    meta: ReportMeta
  ): Promise<void> {
    // Without this the first page can be drawn with a fallback font, which
    // changes every measured text width and misaligns the table.
    await this.ensureFontsReady()

    const pages: HTMLCanvasElement[] = []
    const renderer = new PageRenderer(meta, pages)

    renderer.startPage()
    renderer.drawReportHeader(meta)
    renderer.drawSummary(report, meta.locale)
    renderer.drawCharts(charts)
    renderer.drawEntityTable(report, meta.locale)
    renderer.finish()

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

    doc.save(meta.fileName)
  }

  private async ensureFontsReady(): Promise<void> {
    try {
      await (document as any).fonts?.ready
    } catch {
      // Font loading API unavailable; system fonts will be used.
    }
  }
}

/**
 * Draws report pages onto canvases, flowing content across page breaks.
 * All horizontal positions are expressed right-to-left.
 */
class PageRenderer {
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private y = 0
  private pageNumber = 0

  /** Right and left edges of the content column. */
  private readonly right = PAGE_WIDTH - MARGIN
  private readonly left = MARGIN

  constructor(
    private meta: ReportMeta,
    private pages: HTMLCanvasElement[]
  ) {}

  startPage(): void {
    this.canvas = document.createElement('canvas')
    this.canvas.width = PAGE_WIDTH * SCALE
    this.canvas.height = PAGE_HEIGHT * SCALE
    this.ctx = this.canvas.getContext('2d')
    this.ctx.scale(SCALE, SCALE)
    this.ctx.direction = 'rtl'
    this.ctx.textBaseline = 'alphabetic'

    this.ctx.fillStyle = '#ffffff'
    this.ctx.fillRect(0, 0, PAGE_WIDTH, PAGE_HEIGHT)

    this.pages.push(this.canvas)
    this.pageNumber = this.pages.length
    this.y = MARGIN

    this.drawPageFooter()
  }

  finish(): void {
    // Page numbers include the total, which is only known once every page
    // exists, so the footers are stamped at the end.
    this.pages.forEach((page, index) => {
      const ctx = page.getContext('2d')
      ctx.save()
      ctx.scale(SCALE, SCALE)
      ctx.direction = 'rtl'
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, PAGE_HEIGHT - MARGIN + 6, PAGE_WIDTH, MARGIN)
      ctx.strokeStyle = COLOR_RULE
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(MARGIN, PAGE_HEIGHT - MARGIN)
      ctx.lineTo(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN)
      ctx.stroke()

      ctx.fillStyle = COLOR_MUTED
      ctx.font = `11px ${FONT_STACK}`
      ctx.textAlign = 'right'
      ctx.fillText(this.meta.title, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN + 20)
      ctx.textAlign = 'left'
      ctx.fillText(
        $localize`Page ${index + 1} of ${this.pages.length}`,
        MARGIN,
        PAGE_HEIGHT - MARGIN + 20
      )
      ctx.restore()
    })
  }

  /** Space still available above the footer rule. */
  private get remaining(): number {
    return PAGE_HEIGHT - MARGIN - 24 - this.y
  }

  private ensureSpace(height: number): void {
    if (this.remaining < height) {
      this.startPage()
    }
  }

  private drawPageFooter(): void {
    // Placeholder rule; the real footer is stamped in finish().
    this.ctx.strokeStyle = COLOR_RULE
    this.ctx.lineWidth = 1
    this.ctx.beginPath()
    this.ctx.moveTo(this.left, PAGE_HEIGHT - MARGIN)
    this.ctx.lineTo(this.right, PAGE_HEIGHT - MARGIN)
    this.ctx.stroke()
  }

  drawReportHeader(meta: ReportMeta): void {
    const ctx = this.ctx
    const bannerHeight = 84

    ctx.fillStyle = COLOR_FOREST_DARK
    ctx.fillRect(0, 0, PAGE_WIDTH, bannerHeight)

    ctx.textAlign = 'right'
    ctx.fillStyle = '#ffffff'
    ctx.font = `600 22px ${FONT_STACK}`
    ctx.fillText(meta.title, this.right, 40)

    ctx.font = `13px ${FONT_STACK}`
    ctx.fillStyle = 'rgba(255,255,255,.82)'
    if (meta.subtitle) {
      ctx.fillText(meta.subtitle, this.right, 64)
    }

    ctx.textAlign = 'left'
    ctx.font = `12px ${FONT_STACK}`
    ctx.fillText(
      new Date().toLocaleString(meta.locale, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }),
      this.left,
      40
    )
    ctx.fillText(meta.periodLabel, this.left, 62)

    this.y = bannerHeight + 28
  }

  private drawSectionTitle(text: string): void {
    this.ensureSpace(48)
    const ctx = this.ctx
    ctx.textAlign = 'right'
    ctx.fillStyle = COLOR_INK
    ctx.font = `600 15px ${FONT_STACK}`
    ctx.fillText(text, this.right, this.y + 14)

    ctx.strokeStyle = COLOR_GOLD
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(this.right, this.y + 22)
    ctx.lineTo(this.right - 46, this.y + 22)
    ctx.stroke()

    this.y += 40
  }

  drawSummary(report: CorrespondenceAnalytics, locale: string): void {
    this.drawSectionTitle($localize`Summary`)

    const number = (value: number | null | undefined): string =>
      value === null || value === undefined
        ? '—'
        : value.toLocaleString(locale, { maximumFractionDigits: 1 })

    const tiles = [
      { label: $localize`Documents`, value: number(report.totals.documents) },
      { label: $localize`Routed`, value: number(report.totals.routed_count) },
      { label: $localize`Returned`, value: number(report.totals.returned_count) },
      {
        label: $localize`Awaiting return`,
        value: number(report.totals.awaiting_return_count),
        accent: COLOR_GOLD,
      },
      {
        label: $localize`Bottlenecked`,
        value: number(report.totals.bottlenecked_count),
        accent: COLOR_UMBER,
      },
      {
        label: $localize`Avg. turnaround (days)`,
        value: number(report.totals.average_turnaround_days),
      },
      {
        label: $localize`Total bottleneck days`,
        value: number(report.totals.total_bottleneck_days),
        accent: COLOR_UMBER,
      },
      {
        label: $localize`Grace period (days)`,
        value: number(report.grace_days),
      },
    ]

    const perRow = 4
    const gap = 12
    const tileWidth = (CONTENT_WIDTH - gap * (perRow - 1)) / perRow
    const tileHeight = 66
    const rows = Math.ceil(tiles.length / perRow)

    this.ensureSpace(rows * (tileHeight + gap))

    const ctx = this.ctx
    tiles.forEach((tile, index) => {
      const row = Math.floor(index / perRow)
      const column = index % perRow
      // RTL: the first tile sits at the right edge.
      const tileRight = this.right - column * (tileWidth + gap)
      const tileLeft = tileRight - tileWidth
      const top = this.y + row * (tileHeight + gap)

      ctx.fillStyle = COLOR_ZEBRA
      this.roundRect(tileLeft, top, tileWidth, tileHeight, 6)
      ctx.fill()

      ctx.fillStyle = tile.accent ?? COLOR_FOREST
      ctx.fillRect(tileRight - 3, top, 3, tileHeight)

      ctx.textAlign = 'right'
      ctx.fillStyle = COLOR_MUTED
      ctx.font = `11px ${FONT_STACK}`
      this.fillClipped(tile.label, tileRight - 12, top + 24, tileWidth - 20)

      ctx.fillStyle = COLOR_INK
      ctx.font = `600 20px ${FONT_STACK}`
      this.fillClipped(tile.value, tileRight - 12, top + 50, tileWidth - 20)
    })

    this.y += rows * (tileHeight + gap) + 12
  }

  drawCharts(charts: ReportChart[]): void {
    const usable = charts.filter((chart) => chart.canvas?.width > 0)
    if (!usable.length) return

    this.drawSectionTitle($localize`Charts`)

    usable.forEach((chart) => {
      const aspect = chart.canvas.height / chart.canvas.width
      const width = CONTENT_WIDTH
      const height = Math.min(Math.round(width * aspect), 260)
      const blockHeight = height + 34

      this.ensureSpace(blockHeight)

      const ctx = this.ctx
      ctx.textAlign = 'right'
      ctx.fillStyle = COLOR_INK
      ctx.font = `600 12px ${FONT_STACK}`
      ctx.fillText(chart.title, this.right, this.y + 12)

      ctx.drawImage(chart.canvas, this.left, this.y + 22, width, height)

      this.y += blockHeight + 10
    })
  }

  drawEntityTable(report: CorrespondenceAnalytics, locale: string): void {
    this.drawSectionTitle($localize`Entity breakdown`)

    const number = (value: number | null | undefined): string =>
      value === null || value === undefined
        ? '—'
        : value.toLocaleString(locale, { maximumFractionDigits: 1 })

    const columns: TableColumn[] = [
      { header: $localize`Entity`, width: 0.24, value: (r) => r.name ?? '—' },
      { header: $localize`Code`, width: 0.1, value: (r) => r.code || '—' },
      {
        header: $localize`Diwan no.`,
        width: 0.11,
        value: (r) => r.diwan_number || '—',
      },
      {
        header: $localize`Sent`,
        width: 0.09,
        value: (r) => number(r.sent_count),
        numeric: true,
      },
      {
        header: $localize`Received`,
        width: 0.1,
        value: (r) => number(r.received_count),
        numeric: true,
      },
      {
        header: $localize`Uploaded`,
        width: 0.1,
        value: (r) => number(r.uploaded_count),
        numeric: true,
      },
      {
        header: $localize`Awaiting`,
        width: 0.08,
        value: (r) => number(r.awaiting_return_count),
        numeric: true,
      },
      {
        header: $localize`Avg. days`,
        width: 0.09,
        value: (r) => number(r.average_turnaround_days),
        numeric: true,
      },
      {
        header: $localize`Bottleneck days`,
        width: 0.09,
        value: (r) => number(r.total_bottleneck_days),
        numeric: true,
      },
    ]

    const rowHeight = 26
    const headerHeight = 30

    if (!report.entities.length) {
      this.ensureSpace(rowHeight)
      const ctx = this.ctx
      ctx.textAlign = 'right'
      ctx.fillStyle = COLOR_MUTED
      ctx.font = `12px ${FONT_STACK}`
      ctx.fillText($localize`No correspondence in this period.`, this.right, this.y + 14)
      this.y += rowHeight
      return
    }

    let index = 0
    while (index < report.entities.length) {
      this.ensureSpace(headerHeight + rowHeight)
      this.drawTableHeader(columns, headerHeight)

      while (index < report.entities.length && this.remaining >= rowHeight) {
        this.drawTableRow(columns, report.entities[index], rowHeight, index)
        index++
      }
    }

    this.y += 8
  }

  private drawTableHeader(columns: TableColumn[], height: number): void {
    const ctx = this.ctx
    ctx.fillStyle = COLOR_FOREST
    ctx.fillRect(this.left, this.y, CONTENT_WIDTH, height)

    ctx.fillStyle = '#ffffff'
    ctx.font = `600 11px ${FONT_STACK}`
    this.forEachCell(columns, (column, cellRight, cellWidth) => {
      ctx.textAlign = column.numeric ? 'center' : 'right'
      const x = column.numeric ? cellRight - cellWidth / 2 : cellRight - 8
      this.fillClipped(column.header, x, this.y + 19, cellWidth - 10)
    })

    this.y += height
  }

  private drawTableRow(
    columns: TableColumn[],
    row: EntityAnalytics,
    height: number,
    index: number
  ): void {
    const ctx = this.ctx

    if (index % 2 === 1) {
      ctx.fillStyle = COLOR_ZEBRA
      ctx.fillRect(this.left, this.y, CONTENT_WIDTH, height)
    }

    ctx.strokeStyle = COLOR_RULE
    ctx.lineWidth = 0.5
    ctx.beginPath()
    ctx.moveTo(this.left, this.y + height)
    ctx.lineTo(this.right, this.y + height)
    ctx.stroke()

    ctx.font = `11px ${FONT_STACK}`
    this.forEachCell(columns, (column, cellRight, cellWidth) => {
      const bottleneckColumn = column.header === $localize`Bottleneck days`
      ctx.fillStyle =
        bottleneckColumn && row.total_bottleneck_days > 0 ? COLOR_UMBER : COLOR_INK
      ctx.textAlign = column.numeric ? 'center' : 'right'
      const x = column.numeric ? cellRight - cellWidth / 2 : cellRight - 8
      this.fillClipped(column.value(row), x, this.y + 17, cellWidth - 10)
    })

    this.y += height
  }

  /** Walks the columns right-to-left, handing each its right edge and width. */
  private forEachCell(
    columns: TableColumn[],
    draw: (column: TableColumn, cellRight: number, cellWidth: number) => void
  ): void {
    let cellRight = this.right
    columns.forEach((column) => {
      const cellWidth = CONTENT_WIDTH * column.width
      draw(column, cellRight, cellWidth)
      cellRight -= cellWidth
    })
  }

  /** Draws text, truncating with an ellipsis rather than overflowing the cell. */
  private fillClipped(
    text: string,
    x: number,
    y: number,
    maxWidth: number
  ): void {
    const ctx = this.ctx
    if (ctx.measureText(text).width <= maxWidth) {
      ctx.fillText(text, x, y)
      return
    }
    let truncated = text
    while (
      truncated.length > 1 &&
      ctx.measureText(`${truncated}…`).width > maxWidth
    ) {
      truncated = truncated.slice(0, -1)
    }
    ctx.fillText(`${truncated}…`, x, y)
  }

  private roundRect(
    x: number,
    y: number,
    width: number,
    height: number,
    radius: number
  ): void {
    const ctx = this.ctx
    ctx.beginPath()
    ctx.moveTo(x + radius, y)
    ctx.lineTo(x + width - radius, y)
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius)
    ctx.lineTo(x + width, y + height - radius)
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height)
    ctx.lineTo(x + radius, y + height)
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius)
    ctx.lineTo(x, y + radius)
    ctx.quadraticCurveTo(x, y, x + radius, y)
    ctx.closePath()
  }
}
