import { MediaThumbnail } from './MediaThumbnail'

interface CollectionMediaGridProps {
  items: MediaItem[]
  emptyMessage: string
  onRemove?: (mediaId: number) => void
}

export function CollectionMediaGrid({ items, emptyMessage, onRemove }: CollectionMediaGridProps) {
  if (items.length === 0) return <div className="collection-empty">{emptyMessage}</div>
  return <div className="collection-media-grid">
    {items.map((item) => <article className="collection-media-card" key={item.id}>
      <div className="collection-media-preview">
        <MediaThumbnail mediaId={item.id} mediaType={item.media_type} />
      </div>
      <div className="collection-media-copy">
        <strong title={item.filename}>{item.filename}</strong>
        <p>{item.media_type === 'photo' ? 'Photo' : 'Video'} <span>·</span> {formatBytes(item.size_bytes)}</p>
        {onRemove && <button className="text-button" onClick={() => onRemove(item.id)}>Remove from album</button>}
      </div>
    </article>)}
  </div>
}

function formatBytes(bytes: number): string {
  if (bytes < 1_000_000) return `${Math.max(1, Math.round(bytes / 1_000))} KB`
  return `${(bytes / 1_000_000).toFixed(1)} MB`
}
