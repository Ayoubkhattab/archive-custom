import { convertToParamMap } from '@angular/router'
import {
  filterRulesFromQueryParams,
  queryParamsFromFilterRules,
} from '../utils/query-params'
import { FILTER_FOLDER_TREE } from './filter-rule-type'
import {
  buildFolderTree,
  flattenFolderTree,
  Folder,
  folderPathLabel,
} from './folder'

const folders: Folder[] = [
  { id: 1, name: 'مشروع', parent: null, document_count: 1 },
  { id: 2, name: 'plans 10', parent: 1, document_count: 2 },
  { id: 3, name: 'plans 9', parent: 1, document_count: 0 },
  { id: 4, name: 'old', parent: 2, document_count: 5 },
  { id: 5, name: 'Archive', parent: null, document_count: 0 },
  // Its parent is not visible to this user.
  { id: 6, name: 'shared', parent: 99, document_count: 3 },
]

describe('folder tree', () => {
  it('should nest folders, sort them and add up counts', () => {
    const tree = buildFolderTree(folders)
    // Arabic collation: Arabic names first, then Latin ones.
    expect(tree.roots.map((n) => n.folder.name)).toEqual([
      'مشروع',
      'Archive',
      'shared',
    ])
    const project = tree.byId.get(1)
    // Numeric order: 9 before 10.
    expect(project.children.map((n) => n.folder.name)).toEqual([
      'plans 9',
      'plans 10',
    ])
    expect(project.totalCount).toEqual(8)
    expect(tree.byId.get(4).depth).toEqual(2)
    expect(folderPathLabel(tree.byId.get(4))).toEqual('مشروع / plans 10 / old')
  })

  it('should not loop on a cycle', () => {
    const tree = buildFolderTree([
      { id: 1, name: 'a', parent: 2 },
      { id: 2, name: 'b', parent: 1 },
      { id: 3, name: 'c', parent: 3 },
    ])
    // Folder 3 is its own parent: shown at the top.
    expect(tree.roots.map((n) => n.folder.id)).toEqual([3])
    expect(flattenFolderTree(tree).length).toEqual(1)
  })

  it('should carry the folder filter in the document list URL', () => {
    const rules = [{ rule_type: FILTER_FOLDER_TREE, value: '4' }]
    expect(queryParamsFromFilterRules(rules)).toEqual({ folder__id__tree: '4' })
    expect(
      filterRulesFromQueryParams(convertToParamMap({ folder__id__tree: '4' }))
    ).toEqual(rules)
  })

  it('should flatten parents before children', () => {
    const order = flattenFolderTree(buildFolderTree(folders)).map(
      (n) => n.folder.id
    )
    expect(order).toEqual([1, 3, 2, 4, 5, 6])
  })
})
