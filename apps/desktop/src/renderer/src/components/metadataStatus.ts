export function metadataStatusLabel(item: MediaItem): string {
  const status = item.metadata_status ?? (
    item.width || item.height || item.capture_date || item.camera_make || item.camera_model
      ? 'partial'
      : 'unavailable'
  )
  if (status === 'extracted') return 'Metadata extracted'
  if (status === 'partial') return 'Some metadata unavailable'
  if (status === 'failed') return 'Metadata extraction failed'
  return 'Metadata unavailable'
}
