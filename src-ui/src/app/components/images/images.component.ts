import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  inject,
  OnInit,
} from '@angular/core'
import { takeUntilDestroyed } from '@angular/core/rxjs-interop'
import { NgbPaginationModule } from '@ng-bootstrap/ng-bootstrap'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { UploadedImage } from 'src/app/data/uploaded-image'
import { ImageService } from 'src/app/services/rest/image.service'
import { ToastService } from 'src/app/services/toast.service'
import { ImageUploaderComponent } from '../common/image-uploader/image-uploader.component'
import { PageHeaderComponent } from '../common/page-header/page-header.component'
import { ResponsiveImageComponent } from '../common/responsive-image/responsive-image.component'

@Component({
  selector: 'pngx-images',
  templateUrl: './images.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    PageHeaderComponent,
    ImageUploaderComponent,
    ResponsiveImageComponent,
    NgbPaginationModule,
    NgxBootstrapIconsModule,
  ],
})
export class ImagesComponent implements OnInit {
  private imageService = inject(ImageService)
  private toastService = inject(ToastService)
  private cdr = inject(ChangeDetectorRef)
  private destroyRef = inject(DestroyRef)

  images: UploadedImage[] = []
  count = 0
  page = 1
  readonly pageSize = 24
  loading = false
  loadError = false

  ngOnInit() {
    this.load()
  }

  load(page = this.page) {
    this.page = page
    this.loading = true
    this.loadError = false
    this.imageService
      .list(page, this.pageSize)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.images = result.results
          this.count = result.count
          this.loading = false
          this.cdr.markForCheck()
        },
        error: () => {
          this.loading = false
          this.loadError = true
          this.cdr.markForCheck()
        },
      })
  }

  onUploaded() {
    this.load(1)
  }

  delete(image: UploadedImage) {
    this.imageService.delete(image.id).subscribe({
      next: () => {
        this.images = this.images.filter((i) => i.id !== image.id)
        this.count--
        this.cdr.markForCheck()
      },
      error: (e) =>
        this.toastService.showError($localize`Error deleting image`, e),
    })
  }
}
