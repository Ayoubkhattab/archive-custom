import { HttpClient } from '@angular/common/http'
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
import {
  Chart,
  ChartConfiguration,
  registerables,
} from 'chart.js'
import { first, Subject, Subscription, takeUntil } from 'rxjs'
import { EmptyStateComponent } from 'src/app/components/common/empty-state/empty-state.component'
import { ComponentWithPermissions } from 'src/app/components/with-permissions/with-permissions.component'
import { WebsocketStatusService } from 'src/app/services/websocket-status.service'
import { environment } from 'src/environments/environment'
import { WidgetFrameComponent } from '../widget-frame/widget-frame.component'

Chart.register(...registerables)

interface DayCount {
  date: string
  count: number
}

interface ActivityStatistics {
  documents_added_last_14_days?: DayCount[]
  task_status_counts?: Record<string, number>
}

// Brand palette (Syrian identity)
const COLOR_FOREST = '#428177'
const COLOR_FOREST_DARK = '#054239'
const COLOR_GOLD = '#988561'
const COLOR_GOLD_LIGHT = '#b9a779'
const COLOR_UMBER = '#6b1f2a'
const COLOR_CHARCOAL = '#3d3a3b'

const TASK_STATUS_COLORS: Record<string, string> = {
  SUCCESS: COLOR_FOREST,
  STARTED: COLOR_GOLD_LIGHT,
  PENDING: COLOR_GOLD,
  FAILURE: COLOR_UMBER,
  RETRY: COLOR_CHARCOAL,
}

const TASK_STATUS_LABELS: Record<string, string> = {
  SUCCESS: $localize`Completed`,
  STARTED: $localize`In progress`,
  PENDING: $localize`Pending`,
  FAILURE: $localize`Failed`,
  RETRY: $localize`Retrying`,
}

@Component({
  selector: 'pngx-activity-widget',
  templateUrl: './activity-widget.component.html',
  styleUrls: ['./activity-widget.component.scss'],
  imports: [WidgetFrameComponent, EmptyStateComponent],
})
export class ActivityWidgetComponent
  extends ComponentWithPermissions
  implements OnInit, AfterViewInit, OnDestroy
{
  private http = inject(HttpClient)
  private websocketConnectionService = inject(WebsocketStatusService)
  private changeDetectorRef = inject(ChangeDetectorRef)

  @ViewChild('trendCanvas') trendCanvasRef: ElementRef<HTMLCanvasElement>
  @ViewChild('taskCanvas') taskCanvasRef: ElementRef<HTMLCanvasElement>

  loading = true
  statistics: ActivityStatistics = {}
  totalTasksTracked = 0

  private trendChart: Chart
  private taskChart: Chart
  private viewInitialized = false
  private subscription: Subscription
  private unsubscribeNotifier: Subject<any> = new Subject()

  ngOnInit(): void {
    this.reload()
    this.subscription = this.websocketConnectionService
      .onDocumentConsumptionFinished()
      .subscribe(() => this.reload())
  }

  ngAfterViewInit(): void {
    this.viewInitialized = true
    this.renderCharts()
  }

  ngOnDestroy(): void {
    this.subscription?.unsubscribe()
    this.unsubscribeNotifier.next(true)
    this.unsubscribeNotifier.complete()
    this.trendChart?.destroy()
    this.taskChart?.destroy()
  }

  reload(): void {
    this.http
      .get<ActivityStatistics>(`${environment.apiBaseUrl}statistics/`)
      .pipe(takeUntil(this.unsubscribeNotifier), first())
      .subscribe((statistics) => {
        this.statistics = statistics
        this.totalTasksTracked = Object.values(
          statistics.task_status_counts ?? {}
        ).reduce((sum, n) => sum + n, 0)
        this.loading = false
        // The canvases only exist in the DOM once `loading` is false (they're
        // behind an @else); force a synchronous re-render so the ViewChild
        // refs are populated before we try to use them.
        this.changeDetectorRef.detectChanges()
        this.renderCharts()
      })
  }

  private renderCharts(): void {
    if (this.loading || !this.viewInitialized) return
    this.renderTrendChart()
    this.renderTaskChart()
  }

  private getCssVar(name: string, fallback: string): string {
    const value = getComputedStyle(document.body).getPropertyValue(name)
    return value?.trim() || fallback
  }

  private renderTrendChart(): void {
    if (!this.trendCanvasRef) return
    const days = this.statistics.documents_added_last_14_days ?? []
    const gridColor = this.getCssVar('--bs-border-color-translucent', 'rgba(0,0,0,.1)')
    const textColor = this.getCssVar('--bs-secondary-color', COLOR_CHARCOAL)

    const config: ChartConfiguration<'bar'> = {
      type: 'bar',
      data: {
        labels: days.map((d) =>
          new Date(d.date).toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
          })
        ),
        datasets: [
          {
            label: $localize`Documents added`,
            data: days.map((d) => d.count),
            backgroundColor: COLOR_FOREST,
            hoverBackgroundColor: COLOR_FOREST_DARK,
            borderRadius: 4,
            maxBarThickness: 22,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: COLOR_FOREST_DARK,
            titleColor: '#fff',
            bodyColor: '#fff',
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: textColor, font: { family: 'ITF Qomra Arabic' } },
          },
          y: {
            beginAtZero: true,
            ticks: { precision: 0, color: textColor },
            grid: { color: gridColor },
          },
        },
      },
    }

    if (this.trendChart) {
      this.trendChart.data = config.data
      this.trendChart.options = config.options
      this.trendChart.update()
    } else {
      this.trendChart = new Chart(this.trendCanvasRef.nativeElement, config)
    }
  }

  private renderTaskChart(): void {
    if (!this.taskCanvasRef) return
    const counts = this.statistics.task_status_counts ?? {}
    const entries = Object.entries(counts).filter(([, count]) => count > 0)

    const config: ChartConfiguration<'doughnut'> = {
      type: 'doughnut',
      data: {
        labels: entries.map(
          ([status]) => TASK_STATUS_LABELS[status] ?? status
        ),
        datasets: [
          {
            data: entries.map(([, count]) => count),
            backgroundColor: entries.map(
              ([status]) => TASK_STATUS_COLORS[status] ?? COLOR_CHARCOAL
            ),
            borderWidth: 2,
            borderColor: this.getCssVar('--bs-body-bg', '#fff'),
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '68%',
        animation: { duration: 300 },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              color: this.getCssVar('--bs-secondary-color', COLOR_CHARCOAL),
              font: { family: 'ITF Qomra Arabic', size: 11 },
              boxWidth: 10,
            },
          },
        },
      },
    }

    if (this.taskChart) {
      this.taskChart.data = config.data
      this.taskChart.options = config.options
      this.taskChart.update()
    } else {
      this.taskChart = new Chart(this.taskCanvasRef.nativeElement, config)
    }
  }
}
