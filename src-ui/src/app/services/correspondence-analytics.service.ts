import { HttpClient, HttpParams } from '@angular/common/http'
import { inject, Injectable } from '@angular/core'
import { Observable } from 'rxjs'
import {
  CorrespondenceAnalytics,
  ReportDateField,
} from 'src/app/data/correspondence-analytics'
import { environment } from 'src/environments/environment'

export interface CorrespondenceReportQuery {
  /** YYYY-MM. Takes precedence over dateFrom/dateTo on the backend. */
  month?: string
  dateFrom?: string
  dateTo?: string
  dateField?: ReportDateField
}

@Injectable({
  providedIn: 'root',
})
export class CorrespondenceAnalyticsService {
  private http = inject(HttpClient)

  get(query: CorrespondenceReportQuery = {}): Observable<CorrespondenceAnalytics> {
    let params = new HttpParams()
    if (query.month) {
      params = params.set('month', query.month)
    } else {
      if (query.dateFrom) params = params.set('date_from', query.dateFrom)
      if (query.dateTo) params = params.set('date_to', query.dateTo)
    }
    if (query.dateField) {
      params = params.set('date_field', query.dateField)
    }
    return this.http.get<CorrespondenceAnalytics>(
      `${environment.apiBaseUrl}correspondence_analytics/`,
      { params }
    )
  }
}
