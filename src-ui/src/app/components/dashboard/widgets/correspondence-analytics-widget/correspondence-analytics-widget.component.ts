import { DecimalPipe } from '@angular/common'
import {
  AfterViewInit,
  ChangeDetectorRef,
  Component,
  ElementRef,
  inject,
  OnDestroy,
  OnInit,
  ViewChild,
} from '@angular/core'
import { FormsModule } from '@angular/forms'
import { Chart, ChartConfiguration, registerables } from 'chart.js'
import { first, Subject, takeUntil } from 'rxjs'
import { EmptyStateComponent } from 'src/app/components/common/empty-state/empty-state.component'
import { ComponentWithPermissions } from 'src/app/components/with-permissions/with-permissions.component'
import {
  CorrespondenceAnalytics,
  REPORT_DATE_FIELD_LABELS,
  ReportDateField,
} from 'src/app/data/correspondence-analytics'
import {
  CorrespondenceAnalyticsService,
  CorrespondenceReportQuery,
} from 'src/app/services/correspondence-analytics.service'
import {
  CorrespondenceReportPdfService,
  ReportChart,
} from 'src/app/services/correspondence-report-pdf.service'
import { SettingsService } from 'src/app/services/settings.service'
import { ToastService } from 'src/app/services/toast.service'
import { WidgetFrameComponent } from '../widget-frame/widget-frame.component'

Chart.register(...registerables)

// Brand palette (Syrian identity), matching the activity widget.
const COLOR_FOREST = '#428177'
const COLOR_FOREST_DARK = '#054239'
const COLOR_GOLD = '#988561'
const COLOR_GOLD_LIGHT = '#b9a779'
const COLOR_UMBER = '#6b1f2a'
const COLOR_CHARCOAL = '#3d3a3b'

/** Categorical series for the type/classification doughnuts. */
const CATEGORY_COLORS = [
  COLOR_FOREST,
  COLOR_GOLD,
  COLOR_UMBER,
  COLOR_FOREST_DARK,
  COLOR_GOLD_LIGHT,
  COLOR_CHARCOAL,
]

/** How many entities to show in the per-entity charts before grouping. */
const ENTITY_CHART_LIMIT = 8

export enum PeriodMode {
  Month = 'month',
  Range = 'range',
}

@Component({
  selector: 'pngx-correspondence-analytics-widget',
  templateUrl: './correspondence-analytics-widget.component.html',
  styleUrls: ['./correspondence-analytics-widget.component.scss'],
  imports: [
    WidgetFrameComponent,
    EmptyStateComponent,
    FormsModule,
    DecimalPipe,
  ],
})
export class CorrespondenceAnalyticsWidgetComponent
  extends ComponentWithPermissions
  implements OnInit, AfterViewInit, OnDestroy
{
  private analyticsService = inject(CorrespondenceAnalyticsService)
  private pdfService = inject(CorrespondenceReportPdfService)
  private settingsService = inject(SettingsService)
  private toastService = inject(ToastService)
  private changeDetectorRef = inject(ChangeDetectorRef)

  @ViewChild('entityCanvas') entityCanvasRef: ElementRef<HTMLCanvasElement>
  @ViewChild('trendCanvas') trendCanvasRef: ElementRef<HTMLCanvasElement>
  @ViewChild('bottleneckCanvas') bottleneckCanvasRef: ElementRef<HTMLCanvasElement>
  @ViewChild('breakdownCanvas') breakdownCanvasRef: ElementRef<HTMLCanvasElement>

  public loading = true
  public exporting = false
  public error: string = null
  public report: CorrespondenceAnalytics = null

  public PeriodMode = PeriodMode
  public ReportDateField = ReportDateField
  public dateFieldOptions = Object.values(ReportDateField).map((value) => ({
    value,
    label: REPORT_DATE_FIELD_LABELS[value],
  }))

  public periodMode: PeriodMode = PeriodMode.Month
  public month: string = toMonthInput(new Date())
  public dateFrom: string = toDateInput(startOfMonth(new Date()))
  public dateTo: string = toDateInput(new Date())
  public dateField: ReportDateField = ReportDateField.Added

  private entityChart: Chart
  private trendChart: Chart
  private bottleneckChart: Chart
  private breakdownChart: Chart

  private viewInitialized = false
  private unsubscribeNotifier: Subject<any> = new Subject()

  ngOnInit(): void {
    this.reload()
  }

  ngAfterViewInit(): void {
    this.viewInitialized = true
    this.renderCharts()
  }

  ngOnDestroy(): void {
    this.unsubscribeNotifier.next(true)
    this.unsubscribeNotifier.complete()
    this.destroyCharts()
  }

  get periodLabel(): string {
    if (!this.report) return ''
    const locale = this.locale
    const from = new Date(this.report.period.date_from)
    const to = new Date(this.report.period.date_to)
    const options: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }
    return $localize`${from.toLocaleDateString(locale, options)} to ${to.toLocaleDateString(locale, options)}`
  }

  get dateFieldLabel(): string {
    return REPORT_DATE_FIELD_LABELS[this.dateField]
  }

  private get locale(): string {
    return this.settingsService.getLanguage() || navigator.language || 'en'
  }

  /**
   * Validates the range before hitting the API so an inverted range is caught
   * in the form rather than coming back as a request error.
   */
  get rangeIsValid(): boolean {
    if (this.periodMode === PeriodMode.Month) {
      return /^\d{4}-\d{2}$/.test(this.month ?? '')
    }
    if (!this.dateFrom || !this.dateTo) return false
    return this.dateFrom <= this.dateTo
  }

  onPeriodChange(): void {
    if (this.rangeIsValid) {
      this.reload()
    }
  }

  reload(): void {
    if (!this.rangeIsValid) return
    this.loading = true
    this.error = null

    const query: CorrespondenceReportQuery =
      this.periodMode === PeriodMode.Month
        ? { month: this.month, dateField: this.dateField }
        : {
            dateFrom: this.dateFrom,
            dateTo: this.dateTo,
            dateField: this.dateField,
          }

    this.analyticsService
      .get(query)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe({
        next: (report) => {
          this.report = report
          this.loading = false
          // The canvases only exist once loading is false, so force a
          // synchronous pass before reaching for the ViewChild refs.
          this.changeDetectorRef.detectChanges()
          this.renderCharts()
        },
        error: (e) => {
          this.loading = false
          this.report = null
          this.error = $localize`Could not load the correspondence report.`
          this.toastService.showError(this.error, e)
          this.changeDetectorRef.detectChanges()
        },
      })
  }

  async exportPdf(): Promise<void> {
    if (!this.report || this.exporting) return
    this.exporting = true
    try {
      const charts: ReportChart[] = [
        {
          title: $localize`Sent vs. received per entity`,
          canvas: this.entityCanvasRef?.nativeElement,
        },
        {
          title: $localize`Monthly volume`,
          canvas: this.trendCanvasRef?.nativeElement,
        },
        {
          title: $localize`Bottleneck days per entity`,
          canvas: this.bottleneckCanvasRef?.nativeElement,
        },
        {
          title: $localize`Documents by type`,
          canvas: this.breakdownCanvasRef?.nativeElement,
        },
      ].filter((chart) => !!chart.canvas)

      await this.pdfService.export(this.report, charts, {
        title: $localize`Correspondence report`,
        subtitle: $localize`Entities, routing and bottleneck analysis`,
        periodLabel: `${this.dateFieldLabel} · ${this.periodLabel}`,
        locale: this.locale,
        fileName: `correspondence-report-${this.report.period.date_from}_${this.report.period.date_to}.pdf`,
      })
      this.toastService.showInfo($localize`Report exported.`)
    } catch (e) {
      this.toastService.showError($localize`Could not export the report.`, e)
    } finally {
      this.exporting = false
      this.changeDetectorRef.detectChanges()
    }
  }

  private destroyCharts(): void {
    this.entityChart?.destroy()
    this.trendChart?.destroy()
    this.bottleneckChart?.destroy()
    this.breakdownChart?.destroy()
    this.entityChart = null
    this.trendChart = null
    this.bottleneckChart = null
    this.breakdownChart = null
  }

  private getCssVar(name: string, fallback: string): string {
    const value = getComputedStyle(document.body).getPropertyValue(name)
    return value?.trim() || fallback
  }

  private get textColor(): string {
    return this.getCssVar('--bs-secondary-color', COLOR_CHARCOAL)
  }

  private get gridColor(): string {
    return this.getCssVar('--bs-border-color-translucent', 'rgba(0,0,0,.1)')
  }

  private renderCharts(): void {
    if (this.loading || !this.viewInitialized || !this.report) return
    this.renderEntityChart()
    this.renderTrendChart()
    this.renderBottleneckChart()
    this.renderBreakdownChart()
  }

  /** The entities carrying the most traffic, which is what the charts show. */
  private get topEntities() {
    return [...this.report.entities]
      .sort(
        (a, b) =>
          b.sent_count + b.received_count - (a.sent_count + a.received_count)
      )
      .slice(0, ENTITY_CHART_LIMIT)
  }

  private renderEntityChart(): void {
    if (!this.entityCanvasRef) return
    const entities = this.topEntities

    const config: ChartConfiguration<'bar'> = {
      type: 'bar',
      data: {
        labels: entities.map((e) => e.code || e.name),
        datasets: [
          {
            label: $localize`Received`,
            data: entities.map((e) => e.received_count),
            backgroundColor: COLOR_FOREST,
            borderRadius: 4,
            maxBarThickness: 18,
          },
          {
            label: $localize`Sent`,
            data: entities.map((e) => e.sent_count),
            backgroundColor: COLOR_GOLD,
            borderRadius: 4,
            maxBarThickness: 18,
          },
          {
            label: $localize`Uploaded`,
            data: entities.map((e) => e.uploaded_count),
            backgroundColor: COLOR_FOREST_DARK,
            borderRadius: 4,
            maxBarThickness: 18,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              color: this.textColor,
              font: { family: 'ITF Qomra Arabic', size: 11 },
              boxWidth: 10,
            },
          },
          tooltip: {
            backgroundColor: COLOR_FOREST_DARK,
            titleColor: '#fff',
            bodyColor: '#fff',
            callbacks: {
              title: (items) =>
                entities[items[0].dataIndex]?.name ?? items[0].label,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: this.textColor,
              font: { family: 'ITF Qomra Arabic' },
            },
          },
          y: {
            beginAtZero: true,
            ticks: { precision: 0, color: this.textColor },
            grid: { color: this.gridColor },
          },
        },
      },
    }

    this.entityChart = this.upsert(
      this.entityChart,
      this.entityCanvasRef.nativeElement,
      config
    )
  }

  private renderTrendChart(): void {
    if (!this.trendCanvasRef) return
    const points = this.report.monthly ?? []
    const locale = this.locale

    const config: ChartConfiguration<'line'> = {
      type: 'line',
      data: {
        labels: points.map((p) => {
          const [year, month] = p.month.split('-').map(Number)
          return new Date(year, month - 1, 1).toLocaleDateString(locale, {
            month: 'short',
            year: '2-digit',
          })
        }),
        datasets: [
          {
            label: $localize`Received`,
            data: points.map((p) => p.received),
            borderColor: COLOR_FOREST,
            backgroundColor: 'rgba(66,129,119,.15)',
            fill: true,
            tension: 0.3,
            pointRadius: 2,
          },
          {
            label: $localize`Sent`,
            data: points.map((p) => p.sent),
            borderColor: COLOR_GOLD,
            backgroundColor: 'rgba(152,133,97,.15)',
            fill: true,
            tension: 0.3,
            pointRadius: 2,
          },
          {
            label: $localize`Uploaded`,
            data: points.map((p) => p.uploaded),
            borderColor: COLOR_UMBER,
            backgroundColor: 'rgba(107,31,42,.12)',
            fill: true,
            tension: 0.3,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              color: this.textColor,
              font: { family: 'ITF Qomra Arabic', size: 11 },
              boxWidth: 10,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: this.textColor,
              font: { family: 'ITF Qomra Arabic' },
            },
          },
          y: {
            beginAtZero: true,
            ticks: { precision: 0, color: this.textColor },
            grid: { color: this.gridColor },
          },
        },
      },
    }

    this.trendChart = this.upsert(
      this.trendChart,
      this.trendCanvasRef.nativeElement,
      config
    )
  }

  private renderBottleneckChart(): void {
    if (!this.bottleneckCanvasRef) return
    const entities = [...this.report.entities]
      .filter((e) => e.total_bottleneck_days > 0)
      .sort((a, b) => b.total_bottleneck_days - a.total_bottleneck_days)
      .slice(0, ENTITY_CHART_LIMIT)

    const config: ChartConfiguration<'bar'> = {
      type: 'bar',
      data: {
        labels: entities.map((e) => e.code || e.name),
        datasets: [
          {
            label: $localize`Bottleneck days`,
            data: entities.map((e) => e.total_bottleneck_days),
            backgroundColor: COLOR_UMBER,
            borderRadius: 4,
            maxBarThickness: 20,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: COLOR_FOREST_DARK,
            titleColor: '#fff',
            bodyColor: '#fff',
            callbacks: {
              title: (items) =>
                entities[items[0].dataIndex]?.name ?? items[0].label,
              afterBody: (items) => {
                const entity = entities[items[0].dataIndex]
                if (!entity) return ''
                return $localize`Average turnaround: ${entity.average_turnaround_days ?? '—'} days`
              },
            },
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            ticks: { precision: 0, color: this.textColor },
            grid: { color: this.gridColor },
          },
          y: {
            grid: { display: false },
            ticks: {
              color: this.textColor,
              font: { family: 'ITF Qomra Arabic' },
            },
          },
        },
      },
    }

    this.bottleneckChart = this.upsert(
      this.bottleneckChart,
      this.bottleneckCanvasRef.nativeElement,
      config
    )
  }

  private renderBreakdownChart(): void {
    if (!this.breakdownCanvasRef) return
    const entries = (this.report.by_document_type ?? []).slice(0, 6)

    const config: ChartConfiguration<'doughnut'> = {
      type: 'doughnut',
      data: {
        labels: entries.map((e) => e.name ?? $localize`Unassigned`),
        datasets: [
          {
            data: entries.map((e) => e.count),
            backgroundColor: entries.map(
              (_, index) => CATEGORY_COLORS[index % CATEGORY_COLORS.length]
            ),
            borderWidth: 2,
            borderColor: '#ffffff',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '62%',
        animation: { duration: 300 },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              color: this.textColor,
              font: { family: 'ITF Qomra Arabic', size: 11 },
              boxWidth: 10,
            },
          },
        },
      },
    }

    this.breakdownChart = this.upsert(
      this.breakdownChart,
      this.breakdownCanvasRef.nativeElement,
      config
    )
  }

  private upsert(
    chart: Chart,
    canvas: HTMLCanvasElement,
    config: ChartConfiguration<any>
  ): Chart {
    if (chart) {
      chart.data = config.data
      chart.options = config.options
      chart.update()
      return chart
    }
    return new Chart(canvas, config)
  }
}

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

/** Local-time YYYY-MM-DD, avoiding the UTC shift of toISOString(). */
function toDateInput(date: Date): string {
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

function toMonthInput(date: Date): string {
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  return `${date.getFullYear()}-${month}`
}
