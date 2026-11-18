import { ChangeDetectionStrategy, Component, Input } from '@angular/core'
import { UploadedImage } from 'src/app/data/uploaded-image'

/**
 * Responsive, layout-stable image: srcset/sizes from the generated variants,
 * explicit width/height (no layout shift), lazy loading, async decoding.
 */
@Component({
  selector: 'pngx-responsive-image',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (image?.variants?.length) {
      <img
        class="img-fluid"
        [attr.src]="fallbackSrc"
        [attr.srcset]="image.srcset"
        [attr.sizes]="sizes"
        [attr.width]="image.width"
        [attr.height]="image.height"
        [attr.loading]="eager ? 'eager' : 'lazy'"
        [attr.fetchpriority]="eager ? 'high' : null"
        decoding="async"
        [alt]="alt || image.alt_text || image.original_name"
      />
    } @else {
      <div
        class="placeholder-glow bg-body-secondary rounded"
        [style.aspect-ratio]="(image?.width || 4) + ' / ' + (image?.height || 3)"
        role="img"
        [attr.aria-label]="alt || image?.alt_text || image?.original_name"
      ></div>
    }
  `,
})
export class ResponsiveImageComponent {
  @Input() image: UploadedImage
  @Input() sizes = '(max-width: 576px) 100vw, (max-width: 992px) 50vw, 33vw'
  @Input() alt: string
  /** Use for the LCP image only. */
  @Input() eager = false

  get fallbackSrc(): string {
    const variants = this.image?.variants ?? []
    const small = variants.find((v) => v.kind === 'small')
    return (small ?? variants[variants.length - 1])?.url ?? null
  }
}
