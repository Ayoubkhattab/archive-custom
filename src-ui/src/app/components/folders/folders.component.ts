import {
  Component,
  ElementRef,
  inject,
  OnDestroy,
  OnInit,
  ViewChild,
} from '@angular/core'
import { NgTemplateOutlet } from '@angular/common'
import { FormsModule } from '@angular/forms'
import { ActivatedRoute, Router, RouterModule } from '@angular/router'
import { NgbPaginationModule } from '@ng-bootstrap/ng-bootstrap'
import { NgxBootstrapIconsModule } from 'ngx-bootstrap-icons'
import { Subject, Subscription } from 'rxjs'
import { switchMap, takeUntil } from 'rxjs/operators'
import { Document } from 'src/app/data/document'
import {
  buildFolderTree,
  Folder,
  FolderNode,
  FolderTree,
} from 'src/app/data/folder'
import { CustomDatePipe } from 'src/app/pipes/custom-date.pipe'
import { DialogService } from 'src/app/services/dialog.service'
import {
  PermissionAction,
  PermissionsService,
  PermissionType,
} from 'src/app/services/permissions.service'
import { DocumentService } from 'src/app/services/rest/document.service'
import { FolderService } from 'src/app/services/rest/folder.service'
import { ToastService } from 'src/app/services/toast.service'
import { UploadDocumentsService } from 'src/app/services/upload-documents.service'
import { PageHeaderComponent } from '../common/page-header/page-header.component'

export const FOLDER_PAGE_SIZE = 50

/**
 * Browsing documents by folder, as in a file manager: the tree on the side,
 * the open folder's subfolders and documents in the middle. A ZIP uploaded
 * here is unpacked into a subfolder of the open folder.
 */
@Component({
  selector: 'pngx-folders',
  templateUrl: './folders.component.html',
  styleUrls: ['./folders.component.scss'],
  imports: [
    CustomDatePipe,
    FormsModule,
    NgbPaginationModule,
    NgTemplateOutlet,
    NgxBootstrapIconsModule,
    PageHeaderComponent,
    RouterModule,
  ],
})
export class FoldersComponent implements OnInit, OnDestroy {
  private folderService = inject(FolderService)
  private documentService = inject(DocumentService)
  private uploadService = inject(UploadDocumentsService)
  private dialogService = inject(DialogService)
  private toastService = inject(ToastService)
  private permissionsService = inject(PermissionsService)
  private route = inject(ActivatedRoute)
  private router = inject(Router)

  readonly pageSize = FOLDER_PAGE_SIZE

  tree: FolderTree = { roots: [], byId: new Map() }
  loadingFolders = true
  /** The open folder; undefined at the top level. */
  current?: FolderNode
  currentId: number | null = null
  /** Ids of the folders shown open in the side tree. */
  expanded = new Set<number>()

  documents: Document[] = []
  documentCount = 0
  page = 1
  loadingDocuments = false
  includeSubfolders = false

  @ViewChild('fileInput') private fileInput?: ElementRef<HTMLInputElement>

  private unsubscribe = new Subject<void>()
  private documentsSubscription?: Subscription

  get canUpload(): boolean {
    return this.permissionsService.currentUserCan(
      PermissionAction.Add,
      PermissionType.Document
    )
  }

  get canChangeCurrent(): boolean {
    return !!this.current?.folder.user_can_change
  }

  /** Folders shown in the middle: the open folder's, or the top level. */
  get subfolders(): FolderNode[] {
    return this.current ? this.current.children : this.tree.roots
  }

  /** From the top level down to the open folder. */
  get breadcrumbs(): FolderNode[] {
    const trail: FolderNode[] = []
    let node = this.current
    while (node) {
      trail.unshift(node)
      node =
        node.folder.parent != null
          ? this.tree.byId.get(node.folder.parent)
          : undefined
    }
    return trail
  }

  ngOnInit(): void {
    this.route.paramMap
      .pipe(takeUntil(this.unsubscribe))
      .subscribe((params) => {
        const id = params.get('id')
        this.currentId = id ? parseInt(id, 10) : null
        this.page = 1
        this.resolveCurrent()
        this.loadDocuments()
      })
    this.reloadFolders()
  }

  ngOnDestroy(): void {
    this.unsubscribe.next()
    this.unsubscribe.complete()
    this.documentsSubscription?.unsubscribe()
  }

  reloadFolders(): void {
    this.loadingFolders = true
    this.folderService
      .list()
      .pipe(takeUntil(this.unsubscribe))
      .subscribe({
        next: (folders) => {
          this.tree = buildFolderTree(folders)
          this.loadingFolders = false
          this.resolveCurrent()
        },
        error: (error) => {
          this.loadingFolders = false
          this.toastService.showError('تعذّر تحميل المجلدات', error)
        },
      })
  }

  open(node: FolderNode | null): void {
    this.router.navigate(node ? ['/folders', node.folder.id] : ['/folders'])
  }

  toggle(node: FolderNode, event?: Event): void {
    event?.stopPropagation()
    if (this.expanded.has(node.folder.id)) this.expanded.delete(node.folder.id)
    else this.expanded.add(node.folder.id)
  }

  setIncludeSubfolders(value: boolean): void {
    this.includeSubfolders = value
    this.page = 1
    this.loadDocuments()
  }

  onPageChange(page: number): void {
    this.page = page
    this.loadDocuments()
  }

  loadDocuments(): void {
    this.documentsSubscription?.unsubscribe()
    if (this.currentId === null) {
      this.documents = []
      this.documentCount = 0
      return
    }
    this.loadingDocuments = true
    const params = this.includeSubfolders
      ? { folder__id__tree: this.currentId }
      : { folder__id: this.currentId }
    this.documentsSubscription = this.documentService
      .listFiltered(this.page, this.pageSize, 'title', false, [], {
        ...params,
        truncate_content: true,
      })
      .subscribe({
        next: (result) => {
          this.documents = result.results
          this.documentCount = result.count
          this.loadingDocuments = false
        },
        error: (error) => {
          this.loadingDocuments = false
          this.toastService.showError('تعذّر تحميل مستندات المجلد', error)
        },
      })
  }

  async createFolder(): Promise<void> {
    const name = await this.dialogService.prompt({
      title: 'مجلد جديد',
      message: this.current
        ? `داخل «${this.current.folder.name}»`
        : 'في المستوى الأعلى',
      inputLabel: 'اسم المجلد',
      required: true,
      maxLength: 255,
      icon: 'folder-plus',
      confirmLabel: 'إنشاء',
      cancelLabel: 'إلغاء',
    })
    if (!name?.trim()) return
    this.folderService
      .create(name.trim(), this.currentId)
      .pipe(switchMap(() => this.folderService.list()))
      .subscribe({
        next: (folders) => {
          this.tree = buildFolderTree(folders)
          if (this.current) this.expanded.add(this.current.folder.id)
          this.resolveCurrent()
        },
        error: (error) =>
          this.toastService.showError(this.describeError(error), error),
      })
  }

  async renameFolder(node: FolderNode): Promise<void> {
    const name = await this.dialogService.prompt({
      title: 'إعادة تسمية المجلد',
      value: node.folder.name,
      inputLabel: 'الاسم الجديد',
      required: true,
      maxLength: 255,
      icon: 'pencil',
      confirmLabel: 'حفظ',
      cancelLabel: 'إلغاء',
    })
    if (!name?.trim() || name.trim() === node.folder.name) return
    this.updateFolder(node.folder, { name: name.trim() })
  }

  /** Moves the folder up one level (out of its parent). */
  moveUp(node: FolderNode): void {
    const parent =
      node.folder.parent != null
        ? this.tree.byId.get(node.folder.parent)
        : undefined
    if (!parent) return
    this.updateFolder(node.folder, { parent: parent.folder.parent ?? null })
  }

  async deleteFolder(node: FolderNode): Promise<void> {
    const subfolders = node.children.length
    const confirmed = await this.dialogService.confirm({
      title: `حذف المجلد «${node.folder.name}»`,
      message:
        (subfolders
          ? `سيُحذف معه ${subfolders} مجلد فرعي وكل ما تحتها من مجلدات. `
          : '') + 'المستندات لن تُحذف: ستبقى في الأرشيف خارج أي مجلد.',
      tone: 'danger',
      icon: 'trash',
      confirmLabel: 'حذف المجلد',
      cancelLabel: 'إلغاء',
    })
    if (!confirmed) return
    const parent = node.folder.parent
    this.folderService.delete(node.folder.id).subscribe({
      next: () => {
        if (this.isInside(node)) {
          this.open(
            parent != null ? (this.tree.byId.get(parent) ?? null) : null
          )
        }
        this.reloadFolders()
      },
      error: (error) =>
        this.toastService.showError(this.describeError(error), error),
    })
  }

  chooseFiles(): void {
    this.fileInput?.nativeElement.click()
  }

  onFilesChosen(event: Event): void {
    const input = event.target as HTMLInputElement
    const files = Array.from(input.files ?? [])
    input.value = ''
    if (!files.length) return
    files.forEach((file) =>
      this.uploadService.uploadFile(file, {
        folder: this.currentId ?? undefined,
      })
    )
    const zips = files.filter((f) => /\.zip$/i.test(f.name)).length
    this.toastService.showInfo(
      zips
        ? `بدأ رفع ${files.length} ملف. ملفات ZIP ستُفكّ إلى مجلدات بعد المعالجة، ثم حدّث الصفحة لرؤيتها.`
        : `بدأ رفع ${files.length} ملف إلى هذا المجلد.`
    )
  }

  /** The document list, filtered to this folder and its subfolders. */
  showInDocumentList(): void {
    if (this.currentId === null) return
    this.router.navigate(['/documents'], {
      queryParams: { folder__id__tree: this.currentId },
    })
  }

  thumbUrl(document: Document): string {
    return this.documentService.getThumbUrl(document.id)
  }

  downloadUrl(document: Document): string {
    return this.documentService.getDownloadUrl(document.id, true)
  }

  fileExtension(document: Document): string {
    const name = document.original_file_name ?? ''
    const dot = name.lastIndexOf('.')
    return dot > 0 ? name.slice(dot + 1).toUpperCase() : ''
  }

  /** The folder a document is in, when subfolders are included. */
  documentFolderName(document: Document): string {
    if (!this.includeSubfolders || document.folder === this.currentId) return ''
    return this.tree.byId.get(document.folder)?.folder.name ?? ''
  }

  private updateFolder(folder: Folder, changes: Partial<Folder>): void {
    this.folderService.update(folder.id, changes).subscribe({
      next: () => this.reloadFolders(),
      error: (error) =>
        this.toastService.showError(this.describeError(error), error),
    })
  }

  private isInside(node: FolderNode): boolean {
    return this.breadcrumbs.some((b) => b.folder.id === node.folder.id)
  }

  private resolveCurrent(): void {
    this.current =
      this.currentId !== null ? this.tree.byId.get(this.currentId) : undefined
    // Show the way to the open folder in the side tree.
    this.breadcrumbs
      .slice(0, -1)
      .forEach((node) => this.expanded.add(node.folder.id))
  }

  private describeError(error: any): string {
    const body = error?.error
    const first = (value: unknown) =>
      Array.isArray(value) ? value[0] : typeof value === 'string' ? value : null
    return (
      first(body?.name) ??
      first(body?.parent) ??
      first(body?.detail) ??
      first(body?.non_field_errors) ??
      'تعذّر تنفيذ العملية على المجلد'
    )
  }
}
