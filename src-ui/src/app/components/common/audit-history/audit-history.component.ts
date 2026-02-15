import { DatePipe, KeyValuePipe, TitleCasePipe } from '@angular/common'
import { Component, Input, OnInit } from '@angular/core'
import { NgbTooltipModule } from '@ng-bootstrap/ng-bootstrap'
import { Observable } from 'rxjs'
import { AuditLogAction, AuditLogEntry } from 'src/app/data/auditlog-entry'
import { CustomDatePipe } from 'src/app/pipes/custom-date.pipe'

@Component({
  selector: 'pngx-audit-history',
  templateUrl: './audit-history.component.html',
  styleUrl: './audit-history.component.scss',
  imports: [
    CustomDatePipe,
    DatePipe,
    NgbTooltipModule,
    KeyValuePipe,
    TitleCasePipe,
  ],
})
export class AuditHistoryComponent implements OnInit {
  public AuditLogAction = AuditLogAction

  @Input() entries$: Observable<AuditLogEntry[]>

  public loading: boolean = true
  public entries: AuditLogEntry[] = []

  ngOnInit(): void {
    if (this.entries$) {
      this.loading = true
      this.entries$.subscribe((auditLogEntries) => {
        this.entries = auditLogEntries
        this.loading = false
      })
    }
  }
}
