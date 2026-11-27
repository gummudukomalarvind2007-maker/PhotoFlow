import { useCallback, useEffect, useRef, useState } from 'react'
import { CollectionMediaGrid } from './CollectionMediaGrid'
import { Icon } from './Icon'
import { MediaThumbnail } from './MediaThumbnail'

type SelectedCollection =
  | { type: 'live'; collection: SmartCollection }
  | { type: 'saved'; album: SavedSmartAlbum }

const PAGE_SIZE = 60

export function SmartCollections({ scanStatus }: { scanStatus?: ScanStatus['status'] }) {
  const [collections, setCollections] = useState<SmartCollection[]>([])
  const [savedAlbums, setSavedAlbums] = useState<SavedSmartAlbum[]>([])
  const [selected, setSelected] = useState<SelectedCollection>()
  const [items, setItems] = useState<MediaItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingItems, setLoadingItems] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string>()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const offsetRef = useRef(0)
  const requestId = useRef(0)

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true)
    setError(undefined)
    try {
      const [live, saved] = await Promise.all([
        window.photoFlow.listSmartCollections(),
        window.photoFlow.listSavedSmartAlbums()
      ])
      setCollections(live)
      setSavedAlbums(saved)
      setSelected((current) => {
        if (!current) return current
        if (current.type === 'live') {
          const updated = live.find((item) => item.kind === current.collection.kind && item.value === current.collection.value)
          return {
            type: 'live',
            collection: updated ?? { ...current.collection, media_count: 0, cover_media_id: null }
          }
        }
        const updated = saved.find((item) => item.id === current.album.id)
        return updated ? { type: 'saved', album: updated } : current
      })
    } catch (loadError) {
      setError(errorMessage(loadError, 'Could not load Smart Collections.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh, scanStatus])

  const loadPage = useCallback(async (target: SelectedCollection, offset: number, append: boolean): Promise<void> => {
    const request = ++requestId.current
    if (append) setLoadingMore(true)
    else {
      setItems([])
      setLoadingItems(true)
      setError(undefined)
      offsetRef.current = 0
    }
    try {
      const page = target.type === 'live'
        ? await window.photoFlow.listSmartCollectionMedia(target.collection.kind, target.collection.value, PAGE_SIZE, offset)
        : await window.photoFlow.listSavedSmartAlbumMedia(target.album.id, PAGE_SIZE, offset)
      if (request !== requestId.current) return
      setItems((current) => append ? [...current, ...page] : page)
      offsetRef.current = offset + page.length
      setHasMore(page.length === PAGE_SIZE)
    } catch (loadError) {
      if (request === requestId.current) setError(errorMessage(loadError, 'Could not load this collection.'))
    } finally {
      if (request === requestId.current) {
        setLoadingItems(false)
        setLoadingMore(false)
      }
    }
  }, [])

  useEffect(() => {
    if (selected) void loadPage(selected, 0, false)
  }, [loadPage, scanStatus, selected])

  const openLive = (collection: SmartCollection): void => {
    const target: SelectedCollection = { type: 'live', collection }
    setSelected(target)
    setConfirmDelete(false)
  }

  const openSaved = (album: SavedSmartAlbum): void => {
    const target: SelectedCollection = { type: 'saved', album }
    setSelected(target)
    setConfirmDelete(false)
  }

  const loadMore = (): void => {
    if (selected && !loadingMore) void loadPage(selected, offsetRef.current, true)
  }

  const deleteSaved = async (): Promise<void> => {
    if (selected?.type !== 'saved') return
    setError(undefined)
    try {
      await window.photoFlow.deleteSavedSmartAlbum(selected.album.id)
      requestId.current += 1
      setSelected(undefined)
      setItems([])
      setConfirmDelete(false)
      await refresh()
    } catch (deleteError) {
      setError(errorMessage(deleteError, 'Could not delete this saved Smart Album.'))
    }
  }

  const grouped = [
    { title: 'Recent', kinds: ['recently_added', 'recently_viewed'] },
    { title: 'Capture dates', kinds: ['photo_year', 'photo_month'] },
    { title: 'Photo characteristics', kinds: ['screenshots', 'portrait', 'landscape', 'high_resolution', 'similar_dimensions'] },
    { title: 'Large files', kinds: ['large_files'] },
    { title: 'Videos by duration', kinds: ['video_duration'] }
  ]

  return <section className="smart-collections-page">
    <header className="collections-header">
      <div>
        <p className="section-kicker">AUTOMATICALLY ORGANIZED</p>
        <h1>Smart Collections</h1>
        <p>Live, metadata-based views. Collection results update as media is added, viewed, or removed.</p>
      </div>
      {selected && <button className="text-button" onClick={() => { requestId.current += 1; setSelected(undefined); setItems([]); setError(undefined) }}>← All collections</button>}
    </header>

    {error && <p className="app-error collection-error" role="alert">{error}</p>}

    {selected
      ? <section className="smart-collection-detail">
        <div className="album-detail-heading">
          <div>
            <p className="section-kicker">{selected.type === 'live' ? 'LIVE COLLECTION' : 'SAVED SMART ALBUM'}</p>
            <h2>{selected.type === 'live' ? selected.collection.name : selected.album.name}</h2>
            <p>{selected.type === 'live' ? selected.collection.description : savedAlbumDescription(selected.album)}</p>
          </div>
          <span className="collection-count">{(selected.type === 'live' ? selected.collection.media_count : selected.album.media_count).toLocaleString()} items</span>
        </div>
        {selected.type === 'saved' && <div className="smart-collection-actions">
          {!confirmDelete
            ? <button className="quiet-button danger-button" onClick={() => setConfirmDelete(true)}>Delete saved Smart Album</button>
            : <div className="delete-confirm" role="alert">
              <span>Delete this saved filter? Media files and manual albums will not be changed.</span>
              <button className="quiet-button danger-button" onClick={() => void deleteSaved()}>Delete saved filter</button>
              <button className="text-button" onClick={() => setConfirmDelete(false)}>Cancel</button>
            </div>}
        </div>}
        {loadingItems
          ? <div className="collection-empty" role="status">Loading collection items…</div>
          : <CollectionMediaGrid
            items={items}
            emptyMessage={selected.type === 'live' ? emptyMessage(selected.collection.kind) : 'No media currently matches this saved filter. New matching media will appear automatically.'}
          />}
        {hasMore && <div className="load-more-container">
          <button className="quiet-button" onClick={loadMore} disabled={loadingMore}>{loadingMore ? 'Loading more…' : 'Load more'}</button>
        </div>}
      </section>
      : loading
        ? <div className="collection-empty" role="status">Loading Smart Collections…</div>
        : <>
          {grouped.map((group) => {
            const groupCollections = collections.filter((collection) => group.kinds.includes(collection.kind))
            return groupCollections.length > 0 && <section className="smart-collection-section" key={group.title}>
              <div className="smart-collection-section-heading"><h2>{group.title}</h2><span>{groupCollections.length} collections</span></div>
              <div className="smart-collection-grid">
                {groupCollections.map((collection) => <LiveCollectionTile key={`${collection.kind}:${collection.value}`} collection={collection} onClick={() => openLive(collection)} />)}
              </div>
            </section>
          })}
          <section className="smart-collection-section">
            <div className="smart-collection-section-heading"><h2>Saved Smart Albums</h2><span>{savedAlbums.length}</span></div>
            {savedAlbums.length
              ? <div className="smart-collection-grid">{savedAlbums.map((album) => <SavedAlbumTile key={album.id} album={album} onClick={() => openSaved(album)} />)}</div>
              : <div className="collection-empty smart-collections-empty"><Icon name="sparkle" size={27} /><h2>No saved Smart Albums yet</h2><p>Apply filters in Photos or Videos, then choose Save search to create a live collection.</p></div>}
          </section>
        </>}
  </section>
}

function LiveCollectionTile({ collection, onClick }: { collection: SmartCollection; onClick: () => void }) {
  return <button className="smart-collection-card" onClick={onClick}>
    <div className="smart-collection-cover">
      {collection.cover_media_id
        ? <MediaThumbnail mediaId={collection.cover_media_id} mediaType={collection.kind === 'video_duration' ? 'video' : 'photo'} />
        : <Icon name={collection.kind === 'video_duration' ? 'video' : 'sparkle'} size={26} />}
    </div>
    <div className="smart-collection-card-copy">
      <strong>{collection.name}</strong>
      <p>{collection.description}</p>
      <span>{collection.media_count.toLocaleString()} {collection.media_count === 1 ? 'item' : 'items'}</span>
    </div>
    <span className="smart-collection-live">LIVE</span>
  </button>
}

function SavedAlbumTile({ album, onClick }: { album: SavedSmartAlbum; onClick: () => void }) {
  return <button className="smart-collection-card" onClick={onClick}>
    <div className="smart-collection-cover">
      {album.cover_media_id
        ? <MediaThumbnail mediaId={album.cover_media_id} mediaType={album.media_type === 'video' ? 'video' : 'photo'} />
        : <Icon name="search" size={25} />}
    </div>
    <div className="smart-collection-card-copy">
      <strong>{album.name}</strong>
      <p>{savedAlbumDescription(album)}</p>
      <span>{album.media_count.toLocaleString()} {album.media_count === 1 ? 'item' : 'items'}</span>
    </div>
    <span className="smart-collection-live">SAVED</span>
  </button>
}

function savedAlbumDescription(album: SavedSmartAlbum): string {
  if (album.description) return album.description
  const criteria = [
    album.filters.search && `Search: ${album.filters.search}`,
    album.filters.extension,
    album.filters.camera_make,
    album.filters.camera_model,
    album.filters.date_from && `From ${album.filters.date_from}`,
    album.filters.date_to && `To ${album.filters.date_to}`,
    album.filters.min_resolution_px && `${(album.filters.min_resolution_px / 1_000_000).toFixed(1)} MP minimum`,
    album.filters.min_size_bytes && `${Math.round(album.filters.min_size_bytes / (1024 * 1024))} MiB minimum`
  ].filter(Boolean)
  return criteria.length ? criteria.join(' · ') : `${album.media_type === 'all' ? 'Photos and videos' : album.media_type} · All filters`
}

function emptyMessage(kind: SmartCollectionKind): string {
  if (kind === 'recently_viewed') return 'Photos you open in PhotoFlow will appear here for 30 days.'
  if (kind === 'screenshots') return 'No filenames match the supported screenshot prefixes. Renamed files may not be recognized.'
  if (kind === 'photo_year' || kind === 'photo_month') return 'No photos with capture-date metadata match this collection.'
  if (kind === 'similar_dimensions') return 'No two photos currently share the same recorded dimensions.'
  return 'No media currently matches this live collection. Its contents update when matching media is added or removed.'
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
