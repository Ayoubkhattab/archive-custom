import { provideHttpClient } from '@angular/common/http'
import { provideHttpClientTesting } from '@angular/common/http/testing'
import { TestBed } from '@angular/core/testing'
import { ActivatedRoute, convertToParamMap, Router } from '@angular/router'
import { BehaviorSubject, of, throwError } from 'rxjs'
import { Folder } from 'src/app/data/folder'
import { DialogService } from 'src/app/services/dialog.service'
import { PermissionsService } from 'src/app/services/permissions.service'
import { DocumentService } from 'src/app/services/rest/document.service'
import { FolderService } from 'src/app/services/rest/folder.service'
import { ToastService } from 'src/app/services/toast.service'
import { UploadDocumentsService } from 'src/app/services/upload-documents.service'
import { FoldersComponent } from './folders.component'

const folders: Folder[] = [
  {
    id: 1,
    name: 'Project',
    parent: null,
    document_count: 1,
    user_can_change: true,
  },
  { id: 2, name: 'plans', parent: 1, document_count: 2, user_can_change: true },
  { id: 3, name: 'old', parent: 2, document_count: 0, user_can_change: true },
]

describe('FoldersComponent', () => {
  let component: FoldersComponent
  let params: BehaviorSubject<any>
  let folderService: any
  let documentService: any
  let dialog: any
  let router: any
  let upload: any
  let toast: any

  beforeEach(() => {
    params = new BehaviorSubject(convertToParamMap({ id: '2' }))
    folderService = {
      list: jest.fn(() => of(folders)),
      create: jest.fn(() => of({})),
      update: jest.fn(() => of({})),
      delete: jest.fn(() => of({})),
    }
    documentService = {
      listFiltered: jest.fn(() =>
        of({ count: 1, results: [{ id: 7, title: 'site', folder: 3 }] })
      ),
      getThumbUrl: (id: number) => `thumb/${id}`,
      getDownloadUrl: (id: number) => `download/${id}`,
    }
    dialog = { prompt: jest.fn(), confirm: jest.fn() }
    router = { navigate: jest.fn() }
    upload = { uploadFile: jest.fn() }
    toast = { showError: jest.fn(), showInfo: jest.fn() }

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: FolderService, useValue: folderService },
        { provide: DocumentService, useValue: documentService },
        { provide: DialogService, useValue: dialog },
        { provide: Router, useValue: router },
        { provide: UploadDocumentsService, useValue: upload },
        { provide: ToastService, useValue: toast },
        {
          provide: PermissionsService,
          useValue: { currentUserCan: () => true },
        },
        { provide: ActivatedRoute, useValue: { paramMap: params } },
      ],
    })
    // Constructed directly: rendering specs cannot run in this environment.
    component = TestBed.runInInjectionContext(() => new FoldersComponent())
    component.ngOnInit()
  })

  afterEach(() => component.ngOnDestroy())

  it('should open the folder of the route with its documents', () => {
    expect(component.current.folder.name).toEqual('plans')
    expect(component.breadcrumbs.map((b) => b.folder.name)).toEqual([
      'Project',
      'plans',
    ])
    // The way there is shown open in the side tree.
    expect(component.expanded.has(1)).toBeTruthy()
    expect(component.subfolders.map((n) => n.folder.name)).toEqual(['old'])
    expect(documentService.listFiltered).toHaveBeenLastCalledWith(
      1,
      50,
      'title',
      false,
      [],
      { folder__id: 2, truncate_content: true }
    )
  })

  it('should include subfolders on request', () => {
    component.setIncludeSubfolders(true)
    expect(documentService.listFiltered).toHaveBeenLastCalledWith(
      1,
      50,
      'title',
      false,
      [],
      { folder__id__tree: 2, truncate_content: true }
    )
    expect(component.documentFolderName(component.documents[0])).toEqual('old')
  })

  it('should list top-level folders and no documents at the top', () => {
    documentService.listFiltered.mockClear()
    params.next(convertToParamMap({}))
    expect(component.current).toBeUndefined()
    expect(component.subfolders.map((n) => n.folder.name)).toEqual(['Project'])
    expect(component.documents).toEqual([])
    expect(documentService.listFiltered).not.toHaveBeenCalled()
  })

  it('should create a folder inside the open one', async () => {
    dialog.prompt.mockResolvedValue('  New  ')
    await component.createFolder()
    expect(folderService.create).toHaveBeenCalledWith('New', 2)
  })

  it('should not create when the prompt is cancelled', async () => {
    dialog.prompt.mockResolvedValue(null)
    await component.createFolder()
    expect(folderService.create).not.toHaveBeenCalled()
  })

  it('should rename and move up', async () => {
    dialog.prompt.mockResolvedValue('drawings')
    await component.renameFolder(component.current)
    expect(folderService.update).toHaveBeenCalledWith(2, { name: 'drawings' })

    component.moveUp(component.current)
    expect(folderService.update).toHaveBeenCalledWith(2, { parent: null })
  })

  it('should delete after confirming and go to the parent', async () => {
    dialog.confirm.mockResolvedValue(true)
    await component.deleteFolder(component.current)
    expect(folderService.delete).toHaveBeenCalledWith(2)
    expect(router.navigate).toHaveBeenCalledWith(['/folders', 1])
  })

  it('should show the reason a change was refused', async () => {
    dialog.prompt.mockResolvedValue('plans')
    folderService.create.mockReturnValue(
      throwError(() => ({ error: { name: ['exists already'] } }))
    )
    await component.createFolder()
    expect(toast.showError).toHaveBeenCalledWith(
      'exists already',
      expect.anything()
    )
  })

  it('should upload chosen files into the open folder', () => {
    const files = [
      new File(['x'], 'archive.zip'),
      new File(['y'], 'drawing.dwg'),
    ]
    const input = { files, value: 'C:\\fakepath\\archive.zip' }
    component.onFilesChosen({ target: input } as any)
    expect(upload.uploadFile).toHaveBeenCalledTimes(2)
    expect(upload.uploadFile).toHaveBeenCalledWith(files[0], { folder: 2 })
    expect(input.value).toEqual('')
    expect(toast.showInfo).toHaveBeenCalledWith(expect.stringContaining('ZIP'))
  })

  it('should open the document list filtered to the folder', () => {
    component.showInDocumentList()
    expect(router.navigate).toHaveBeenCalledWith(['/documents'], {
      queryParams: { folder__id__tree: 2 },
    })
  })

  it('should show the file type from the original name', () => {
    expect(
      component.fileExtension({ original_file_name: 'Plan.dwg' } as any)
    ).toEqual('DWG')
    expect(
      component.fileExtension({ original_file_name: 'README' } as any)
    ).toEqual('')
  })
})
