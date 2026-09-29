import {
  CdkDragDrop,
  CdkDragEnd,
  CdkDragStart,
  DragDropModule,
  moveItemInArray,
} from '@angular/cdk/drag-drop'
import { Component, inject } from '@angular/core'
import { RouterModule } from '@angular/router'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { TourNgBootstrapModule, TourService } from 'ngx-ui-tour-ng-bootstrap'
import { SavedView } from 'src/app/data/saved-view'
import { CustomDatePipe } from 'src/app/pipes/custom-date.pipe'
import { CommandPaletteService } from 'src/app/services/command-palette.service'
import {
  PermissionAction,
  PermissionsService,
  PermissionType,
} from 'src/app/services/permissions.service'
import { RecentDocumentsService } from 'src/app/services/recent-documents.service'
import { DocumentService } from 'src/app/services/rest/document.service'
import { IfPermissionsDirective } from 'src/app/directives/if-permissions.directive'
import { SavedViewService } from 'src/app/services/rest/saved-view.service'
import { SettingsService } from 'src/app/services/settings.service'
import { ToastService } from 'src/app/services/toast.service'
import { UploadDocumentsService } from 'src/app/services/upload-documents.service'
import { environment } from 'src/environments/environment'
import { EmptyStateComponent } from '../common/empty-state/empty-state.component'
import { LogoComponent } from '../common/logo/logo.component'
import { PageHeaderComponent } from '../common/page-header/page-header.component'
import { ComponentWithPermissions } from '../with-permissions/with-permissions.component'
import { ActivityWidgetComponent } from './widgets/activity-widget/activity-widget.component'
import { CorrespondenceAnalyticsWidgetComponent } from './widgets/correspondence-analytics-widget/correspondence-analytics-widget.component'
import { SavedViewWidgetComponent } from './widgets/saved-view-widget/saved-view-widget.component'
import { StatisticsWidgetComponent } from './widgets/statistics-widget/statistics-widget.component'
import { UploadFileWidgetComponent } from './widgets/upload-file-widget/upload-file-widget.component'
import { WelcomeWidgetComponent } from './widgets/welcome-widget/welcome-widget.component'

@Component({
  selector: 'pngx-dashboard',
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.scss'],
  imports: [
    LogoComponent,
    EmptyStateComponent,
    PageHeaderComponent,
    ActivityWidgetComponent,
    CorrespondenceAnalyticsWidgetComponent,
    SavedViewWidgetComponent,
    StatisticsWidgetComponent,
    UploadFileWidgetComponent,
    WelcomeWidgetComponent,
    IfPermissionsDirective,
    DragDropModule,
    TourNgBootstrapModule,
    NgxBootstrapIconsModule,
    RouterModule,
    CustomDatePipe,
  ],
})
export class DashboardComponent extends ComponentWithPermissions {
  settingsService = inject(SettingsService)
  savedViewService = inject(SavedViewService)
  private tourService = inject(TourService)
  private toastService = inject(ToastService)
  private commandPaletteService = inject(CommandPaletteService)
  private recentDocumentsService = inject(RecentDocumentsService)
  private documentService = inject(DocumentService)
  private uploadDocumentsService = inject(UploadDocumentsService)
  private permissionsService = inject(PermissionsService)

  public dashboardViews: SavedView[] = []
  constructor() {
    super()

    this.savedViewService.listAll().subscribe(() => {
      this.dashboardViews = this.savedViewService.dashboardViews
    })
  }

  get greeting(): string {
    const hour = new Date().getHours()
    if (hour >= 5 && hour < 12) {
      return $localize`:@@dashboard.greeting.morning:Good morning`
    } else if (hour >= 12 && hour < 17) {
      return $localize`:@@dashboard.greeting.afternoon:Good afternoon`
    }
    return $localize`:@@dashboard.greeting.evening:Good evening`
  }

  get canUpload(): boolean {
    return this.permissionsService.currentUserCan(
      PermissionAction.Add,
      PermissionType.Document
    )
  }

  get recentDocuments() {
    if (
      !this.permissionsService.currentUserCan(
        PermissionAction.View,
        PermissionType.Document
      )
    ) {
      return []
    }
    return this.recentDocumentsService.list().slice(0, 8)
  }

  thumbUrl(id: number): string {
    return this.documentService.getThumbUrl(id)
  }

  openCommandPalette() {
    this.commandPaletteService.open()
  }

  onQuickUpload(event: Event) {
    const input = event.target as HTMLInputElement
    Array.from(input.files ?? []).forEach((file) =>
      this.uploadDocumentsService.uploadFile(file)
    )
    input.value = ''
  }

  removeRecentDocument(id: number) {
    this.recentDocumentsService.remove(id)
  }

  clearRecentDocuments() {
    this.recentDocumentsService.clear()
  }

  get subtitle() {
    if (this.settingsService.displayName) {
      return $localize`Hello ${this.settingsService.displayName}, welcome to ${environment.appTitle}`
    } else {
      return $localize`Welcome to ${environment.appTitle}`
    }
  }

  completeTour() {
    if (this.tourService.getStatus() !== 0) {
      this.tourService.end() // will call settingsService.completeTour()
    } else {
      this.settingsService.completeTour()
    }
  }

  onDragStart(event: CdkDragStart) {
    this.settingsService.globalDropzoneEnabled = false
  }

  onDragEnd(event: CdkDragEnd) {
    this.settingsService.globalDropzoneEnabled = true
  }

  onDrop(event: CdkDragDrop<SavedView[]>) {
    moveItemInArray(
      this.dashboardViews,
      event.previousIndex,
      event.currentIndex
    )

    this.settingsService
      .updateDashboardViewsSort(this.dashboardViews)
      .subscribe({
        next: () => {
          this.toastService.showInfo($localize`Dashboard updated`)
        },
        error: (e) => {
          this.toastService.showError($localize`Error updating dashboard`, e)
        },
      })
  }
}
