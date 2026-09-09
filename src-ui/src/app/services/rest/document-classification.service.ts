import { Injectable } from '@angular/core'
import { DocumentClassification } from 'src/app/data/document-classification'
import { AbstractNameFilterService } from './abstract-name-filter-service'

@Injectable({
  providedIn: 'root',
})
export class DocumentClassificationService extends AbstractNameFilterService<DocumentClassification> {
  constructor() {
    super()
    this.resourceName = 'document_classifications'
  }
}
