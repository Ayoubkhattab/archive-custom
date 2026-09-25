import { Component, Input } from '@angular/core'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'

@Component({
  selector: 'pngx-empty-state',
  templateUrl: './empty-state.component.html',
  styleUrls: ['./empty-state.component.scss'],
  imports: [NgxBootstrapIconsModule],
})
export class EmptyStateComponent {
  @Input()
  icon: string = 'inbox'

  @Input()
  message: string
}
