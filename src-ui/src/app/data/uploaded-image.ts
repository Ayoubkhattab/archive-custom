export type ImageVariantKind = 'thumbnail' | 'small' | 'medium' | 'large'

export interface ImageVariant {
  kind: ImageVariantKind
  width: number
  height: number
  format: string
  size_bytes: number
  /** Authenticated, content-addressed API URL (never a storage path). */
  url: string
}

export interface UploadedImage {
  id: string
  owner: number
  original_name: string
  alt_text: string
  mime_type: string
  source_mime_type: string
  size_bytes: number
  width: number
  height: number
  checksum: string
  status: 'pending' | 'ready' | 'failed'
  created_at: string
  variants: ImageVariant[]
  srcset: string
  /** Server-side policy decision for the current user. */
  can_delete: boolean
}

/** Defaults matching the server (PAPERLESS_IMAGES_*); the server re-checks. */
export const IMAGE_UPLOAD_DEFAULTS = {
  maxBytes: 10 * 1024 * 1024,
  acceptedTypes: [
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/avif',
    'image/gif',
  ],
}
