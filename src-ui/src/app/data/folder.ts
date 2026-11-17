/** A folder documents are filed in, such as a folder of an uploaded ZIP. */
export interface Folder {
  id?: number
  name: string
  /** Null for a top-level folder. */
  parent: number | null
  owner?: number | null
  created?: string
  /** Documents directly in this folder that the user may see. */
  document_count?: number
  user_can_change?: boolean
}

/** A folder with its place in the tree, as built by `buildFolderTree`. */
export interface FolderNode {
  folder: Folder
  children: FolderNode[]
  /** Names from the top-level folder down to this one. */
  path: string[]
  depth: number
  /** Documents in this folder and all of its subfolders. */
  totalCount: number
}

export interface FolderTree {
  roots: FolderNode[]
  byId: Map<number, FolderNode>
}

const collator = new Intl.Collator(['ar', 'en'], {
  numeric: true,
  sensitivity: 'base',
})

/**
 * The folders arranged as a tree. A folder whose parent the user cannot see
 * is shown at the top, so nothing visible is left unreachable.
 */
export function buildFolderTree(folders: Folder[]): FolderTree {
  const byId = new Map<number, FolderNode>()
  for (const folder of folders) {
    byId.set(folder.id, {
      folder,
      children: [],
      path: [],
      depth: 0,
      totalCount: 0,
    })
  }
  const roots: FolderNode[] = []
  for (const node of byId.values()) {
    const parent =
      node.folder.parent != null ? byId.get(node.folder.parent) : undefined
    if (parent && parent !== node) parent.children.push(node)
    else roots.push(node)
  }

  const visit = (node: FolderNode, path: string[], seen: Set<number>) => {
    seen.add(node.folder.id)
    node.path = [...path, node.folder.name]
    node.depth = path.length
    node.children = node.children.filter((child) => !seen.has(child.folder.id))
    node.children.sort((a, b) => collator.compare(a.folder.name, b.folder.name))
    node.totalCount = node.folder.document_count ?? 0
    for (const child of node.children) {
      visit(child, node.path, seen)
      node.totalCount += child.totalCount
    }
  }
  roots.sort((a, b) => collator.compare(a.folder.name, b.folder.name))
  const seen = new Set<number>()
  roots.forEach((root) => visit(root, [], seen))
  return { roots, byId }
}

/** "Project / plans / old", for showing a folder in a flat list. */
export function folderPathLabel(node: FolderNode | undefined): string {
  return node ? node.path.join(' / ') : ''
}

/** Every node, parents before their children, in display order. */
export function flattenFolderTree(tree: FolderTree): FolderNode[] {
  const result: FolderNode[] = []
  const walk = (nodes: FolderNode[]) =>
    nodes.forEach((node) => {
      result.push(node)
      walk(node.children)
    })
  walk(tree.roots)
  return result
}
