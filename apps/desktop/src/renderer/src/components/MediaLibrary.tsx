import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { Icon } from './Icon'
import { ScanProgress } from './ScanProgress'
import { metadataStatusLabel } from './metadataStatus'
import { MediaThumbnail } from './MediaThumbnail'
import { thumbnailCache } from './thumbnailCache'
import {
  clearMediaSelection,
  isMediaSelected,
  mediaSelectionForScope,
  mediaSelectionCount,
  selectAllMedia,
  toggleMediaSelection,
  type MediaSelection
} from './mediaSelection'

const PAGE_SIZE = 60
const EXTENSIONS: Record<'all' | 'photo' | 'video', string[]> = {
  all: ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.heic', '.heif', '.mp4', '.mov', '.avi', '.mkv', '.webm'],
  photo: ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.heic', '.heif'],
  video: ['.mp4', '.mov', '.avi', '.mkv', '.webm']
}
type NumericFilterKey = keyof Pick<MediaFilters,
  | 'min_width' | 'max_width' | 'min_height' | 'max_height'
  | 'min_resolution_px' | 'max_resolution_px'
  | 'min_duration_seconds' | 'max_duration_seconds'
  | 'min_size_bytes' | 'max_size_bytes'
>

interface MediaLibraryProps {
  initialType: 'photo' | 'video'
  search: string
  onSearchChange: (value: string) => void
  scan?: ScanStatus
  onAddMedia: () => void
  onCancel: () => void
  scanRunning: boolean
  onOpenAlbums: () => void
  onTransferStarted: (job: TransferJob) => void
  transferBusy: boolean
  onMediaRemoved: () => void
}

export function MediaLibrary({ initialType, search, onSearchChange, scan, onAddMedia, onCancel, scanRunning, onOpenAlbums, onTransferStarted, transferBusy, onMediaRemoved }: MediaLibraryProps) {
  const [mediaType, setMediaType] = useState<'all' | 'photo' | 'video'>(initialType)
  const [extension, setExtension] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [cameraMake, setCameraMake] = useState('')
  const [cameraModel, setCameraModel] = useState('')
  const [numericFilters, setNumericFilters] = useState<Partial<Pick<MediaFilters, NumericFilterKey>>>({})
  const [sort, setSort] = useState<NonNullable<MediaFilters['sort']>>('newest')
  const [debouncedSearch, setDebouncedSearch] = useState(search)
  const [items, setItems] = useState<MediaItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string>()
  const [albumError, setAlbumError] = useState<string>()
  const [albumMessage, setAlbumMessage] = useState<string>()
  const [albums, setAlbums] = useState<Album[]>([])
  const [selection, setSelection] = useState<MediaSelection>(clearMediaSelection())
  const [matchingCount, setMatchingCount] = useState(0)
  const [targetAlbumId, setTargetAlbumId] = useState('')
  const [organizeExports, setOrganizeExports] = useState(true)
  const [reloadToken, setReloadToken] = useState(0)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [removeError, setRemoveError] = useState<string>()
  const [removeMessage, setRemoveMessage] = useState<string>()
  const [viewer, setViewer] = useState<{ item: MediaItem; dataUrl?: string; error?: string; loading: boolean }>()
  const [saveSearchOpen, setSaveSearchOpen] = useState(false)
  const [savedSearchName, setSavedSearchName] = useState('')
  const [savedSearchDescription, setSavedSearchDescription] = useState('')
  const [savedSearchError, setSavedSearchError] = useState<string>()
  const loadingMoreRef = useRef(false)
  const pageOffsetRef = useRef(0)
  const pageVersionRef = useRef(0)
  const itemIdsRef = useRef(new Set<number>())
  const selectionRef = useRef(selection)
  const typeLabel = mediaType === 'all' ? 'Media' : mediaType === 'photo' ? 'Photos' : 'Videos'
  const filters: MediaFilters = {
    search: debouncedSearch,
    extension: extension || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    camera_make: cameraMake.trim() || undefined,
    camera_model: cameraModel.trim() || undefined,
    ...numericFilters,
    sort
  }
  const selectionScope = JSON.stringify([mediaType, filters])
  const activeSelection = mediaSelectionForScope(selection, selectionScope)
  const selectedCount = mediaSelectionCount(activeSelection, matchingCount, selectionScope)
  useLayoutEffect(() => {
    selectionRef.current = selection
  }, [selection])
  const toggleSelection = useCallback((mediaId: number, checked: boolean): void => {
    const currentSelection = mediaSelectionForScope(selectionRef.current, selectionScope)
    if (currentSelection.kind === 'explicit' && checked && currentSelection.ids.length >= 5000) {
      setError('Select no more than 5000 items per export.')
      return
    }
    setSelection((current) => toggleMediaSelection(current, mediaId, checked, selectionScope))
  }, [selectionScope])

  useEffect(() => {
    if (!viewer && !confirmRemove && !saveSearchOpen) return
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      if (viewer) setViewer(undefined)
      else if (confirmRemove && !removing) setConfirmRemove(false)
      else if (saveSearchOpen) setSaveSearchOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [viewer, confirmRemove, removing, saveSearchOpen])
  useEffect(() => setMediaType(initialType), [initialType])
  useEffect(() => {
    void window.photoFlow.listAlbums().then((result) => {
      setAlbums(result)
      setTargetAlbumId((current) => current || (result[0] ? String(result[0].id) : ''))
      setAlbumError(undefined)
    }).catch((loadError: unknown) => setAlbumError(errorMessage(loadError, 'Could not load albums.')))
  }, [scan?.status])
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])
  useEffect(() => {
    if (extension && !EXTENSIONS[mediaType].includes(extension)) setExtension('')
  }, [mediaType, extension])
  useEffect(() => {
    let active = true
    pageVersionRef.current += 1
    const pageVersion = pageVersionRef.current
    loadingMoreRef.current = false
    pageOffsetRef.current = 0
    itemIdsRef.current.clear()
    setLoadingMore(false)
    setItems([])
    setSelection(clearMediaSelection())
    setMatchingCount(0)
    setHasMore(false)
    setError(undefined)
    setLoading(true)
    setRemoveError(undefined)
    setRemoveMessage(undefined)
    void Promise.all([
      window.photoFlow.listMedia(mediaType, PAGE_SIZE, 0, filters),
      window.photoFlow.countMedia(mediaType, filters)
    ]).then(([records, total]) => {
      if (!active || pageVersion !== pageVersionRef.current) return
      setItems(records)
      itemIdsRef.current = new Set(records.map((item) => item.id))
      setMatchingCount(total)
      pageOffsetRef.current = records.length
      setHasMore(records.length === PAGE_SIZE)
      setLoading(false)
    }).catch((loadError: unknown) => {
      if (!active || pageVersion !== pageVersionRef.current) return
      setItems([])
      setError(errorMessage(loadError, `Could not load ${typeLabel.toLowerCase()}.`))
    }).finally(() => {
      if (active && pageVersion === pageVersionRef.current) setLoading(false)
    })
    return () => { active = false }
  }, [mediaType, debouncedSearch, extension, dateFrom, dateTo, cameraMake, cameraModel, numericFilters, sort, scan?.status, typeLabel, reloadToken])

  const loadMore = async (): Promise<void> => {
    if (loadingMoreRef.current || !hasMore) return
    loadingMoreRef.current = true
    const pageVersion = pageVersionRef.current
    setLoadingMore(true)
    setError(undefined)
    try {
      const records = await window.photoFlow.listMedia(mediaType, PAGE_SIZE, pageOffsetRef.current, filters)
      if (pageVersion !== pageVersionRef.current) return
      const newItems = records.filter((item) => !itemIdsRef.current.has(item.id))
      for (const item of newItems) itemIdsRef.current.add(item.id)
      pageOffsetRef.current += records.length
      if (newItems.length) {
        setItems((current) => [...current, ...newItems])
      }
      setHasMore(records.length === PAGE_SIZE)
    } catch (loadError) {
      if (pageVersion === pageVersionRef.current) setError(errorMessage(loadError, `Could not load more ${typeLabel.toLowerCase()}.`))
    } finally {
      if (pageVersion === pageVersionRef.current) {
        loadingMoreRef.current = false
        setLoadingMore(false)
      }
    }
  }

  const clearFilters = (): void => {
    onSearchChange('')
    setMediaType('all')
    setExtension('')
    setDateFrom('')
    setDateTo('')
    setCameraMake('')
    setCameraModel('')
    setNumericFilters({})
    setSort('newest')
  }
  const changeNumericFilter = (key: NumericFilterKey, value: string, multiplier = 1): void => {
    const number = value === '' ? undefined : Number(value) * multiplier
    setNumericFilters((current) => ({
      ...current,
      [key]: number === undefined ? undefined : key.includes('duration') ? number : Math.round(number)
    }))
  }
  const saveSearch = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSavedSearchError(undefined)
    try {
      const album = await window.photoFlow.createSavedSmartAlbum(
        savedSearchName, savedSearchDescription, mediaType, filters
      )
      setAlbumMessage(`Search saved as “${album.name}”. Find it in Smart Collections.`)
      setSaveSearchOpen(false)
      setSavedSearchName('')
      setSavedSearchDescription('')
    } catch (saveError) {
      setSavedSearchError(errorMessage(saveError, 'Could not save this search.'))
    }
  }
  const addSelectedToAlbum = (): void => {
    const albumId = Number(targetAlbumId)
    if (!Number.isSafeInteger(albumId) || albumId < 1 || selectedCount === 0) return
    setAlbumMessage(undefined)
    setAlbumError(undefined)
    void resolveSelectedIds(500).then((ids) => window.photoFlow.addAlbumMedia(albumId, ids)).then((added) => {
        const albumName = albums.find((album) => album.id === albumId)?.name ?? 'album'
        setAlbumMessage(`${added} ${added === 1 ? 'item' : 'items'} added to ${albumName}.`)
        setSelection(clearMediaSelection())
      }).catch((addError: unknown) => setAlbumError(errorMessage(addError, 'Could not add media to this album.')))
  }
  const exportSelected = async (): Promise<void> => {
    setError(undefined)
    try {
      if (activeSelection.kind === 'all' && activeSelection.excludedIds.length === 0 && selectedCount > 5000) {
        const result = await window.photoFlow.exportFilteredMedia(mediaType, filters, organizeExports)
        if (result.job) onTransferStarted(result.job)
        return
      }
      const ids = await resolveSelectedIds(5000)
      const result = await window.photoFlow.exportSelectedMedia(ids, organizeExports)
      if (result.job) onTransferStarted(result.job)
    } catch (exportError) {
      setError(errorMessage(exportError, 'Could not start media export.'))
    }
  }
  const exportMatching = async (): Promise<void> => {
    setError(undefined)
    try {
      const result = await window.photoFlow.exportFilteredMedia(mediaType, filters, organizeExports)
      if (result.job) onTransferStarted(result.job)
    } catch (exportError) {
      setError(errorMessage(exportError, 'Could not start filtered media export.'))
    }
  }
  const resolveSelectedIds = async (maximum: number): Promise<number[]> => {
    if (activeSelection.kind === 'explicit') return activeSelection.ids
    if (selectedCount > maximum) throw new Error(`This action supports up to ${maximum} selected items.`)
    const excluded = new Set(activeSelection.excludedIds)
    const ids: number[] = []
    let offset = 0
    while (offset < matchingCount) {
      const records = await window.photoFlow.listMedia(mediaType, 100, offset, filters)
      if (records.length === 0) break
      ids.push(...records.filter((item) => !excluded.has(item.id)).map((item) => item.id))
      offset += records.length
    }
    if (ids.length !== selectedCount) throw new Error('The library changed while resolving this selection. Refresh and try again.')
    return ids
  }
  const removeSelected = async (): Promise<void> => {
    setRemoving(true)
    setRemoveError(undefined)
    setRemoveMessage(undefined)
    try {
      const payload: MediaRemovalSelection = activeSelection.kind === 'all'
        ? { mode: 'filtered', media_type: mediaType, filters, excluded_ids: activeSelection.excludedIds }
        : { mode: 'ids', media_ids: activeSelection.ids }
      const result = await window.photoFlow.removeMedia(payload)
      thumbnailCache.clear()
      setConfirmRemove(false)
      setSelection(clearMediaSelection())
      setRemoveMessage(`${result.removed} ${result.removed === 1 ? 'item' : 'items'} removed from the library.` +
        (result.thumbnail_cleanup_failed ? ` ${result.thumbnail_cleanup_failed} cached preview${result.thumbnail_cleanup_failed === 1 ? '' : 's'} could not be cleared.` : ''))
      setReloadToken((token) => token + 1)
      onMediaRemoved()
    } catch (removeFailure) {
      setRemoveError(errorMessage(removeFailure, 'Could not remove the selected media from the library.'))
    } finally {
      setRemoving(false)
    }
  }
  const openPhoto = useCallback(async (item: MediaItem): Promise<void> => {
    setViewer({ item, loading: true })
    try {
      const dataUrl = await window.photoFlow.getFullResolution(item.id)
      setViewer((current) => current?.item.id === item.id ? { item, dataUrl, loading: false } : current)
    } catch (viewError) {
      setViewer((current) => current?.item.id === item.id
        ? { item, error: errorMessage(viewError, 'Could not open this photo.'), loading: false }
        : current)
    }
  }, [])
  const hasFilters = Boolean(search || mediaType !== 'all' || extension || dateFrom || dateTo || cameraMake || cameraModel || Object.values(numericFilters).some((value) => value !== undefined) || sort !== 'newest')

  return <section className="media-library">
    <header className="media-header">
      <div>
        <p className="section-kicker">YOUR LIBRARY</p>
        <h1>{typeLabel}</h1>
        <p>{loading ? `Searching local ${typeLabel.toLowerCase()}…` : `${items.length} ${items.length === 1 ? 'item' : 'items'} shown`}</p>
      </div>
      <div className="media-header-actions"><button className="quiet-button" onClick={() => { setSaveSearchOpen(true); setSavedSearchError(undefined) }} disabled={loading || search.trim() !== debouncedSearch}>Save search</button><button className="quiet-button" onClick={() => void exportMatching()} disabled={transferBusy || loading || items.length === 0}>Export matching</button><button className="primary-button" onClick={onAddMedia} disabled={scanRunning}><Icon name="plus" size={18} /> Add media</button></div>
    </header>
    <div className="library-controls" aria-label="Media search, filters and sorting">
      <label className="filter-field">
        <span>Media type</span>
        <select value={mediaType} onChange={(event) => setMediaType(event.target.value as 'all' | 'photo' | 'video')}>
          <option value="all">Photos &amp; videos</option><option value="photo">Photos</option><option value="video">Videos</option>
        </select>
      </label>
      <label className="filter-field">
        <span>Format</span>
        <select value={extension} onChange={(event) => setExtension(event.target.value)}>
          <option value="">All formats</option>
          {EXTENSIONS[mediaType].map((value) => <option key={value} value={value}>{value.slice(1).toUpperCase()}</option>)}
        </select>
      </label>
      <label className="filter-field">
        <span>Captured from</span>
        <input type="date" value={dateFrom} max={dateTo || undefined} onChange={(event) => setDateFrom(event.target.value)} />
      </label>
      <label className="filter-field">
        <span>Captured to</span>
        <input type="date" value={dateTo} min={dateFrom || undefined} onChange={(event) => setDateTo(event.target.value)} />
      </label>
      <label className="filter-field camera-filter">
        <span>Camera make</span>
        <input value={cameraMake} maxLength={100} onChange={(event) => setCameraMake(event.target.value)} placeholder="Any make" />
      </label>
      <label className="filter-field camera-filter">
        <span>Camera model</span>
        <input value={cameraModel} maxLength={100} onChange={(event) => setCameraModel(event.target.value)} placeholder="Any model" />
      </label>
      <label className="filter-field sort-filter">
        <span>Sort by</span>
        <select value={sort} onChange={(event) => setSort(event.target.value as NonNullable<MediaFilters['sort']>)}>
          <option value="newest">Newest first</option><option value="oldest">Oldest first</option>
          <option value="filename-asc">Filename A–Z</option><option value="filename-desc">Filename Z–A</option>
          <option value="size-desc">Largest file first</option><option value="size-asc">Smallest file first</option>
        </select>
      </label>
      <button className="text-button clear-filters" onClick={clearFilters} disabled={!hasFilters}>Clear filters</button>
      <details className="advanced-filters">
        <summary>Dimensions, duration &amp; file size</summary>
        <div className="advanced-filter-grid">
          <label className="filter-field"><span>Minimum width (px)</span><input type="number" min="1" max="100000" step="1" value={numericFilters.min_width ?? ''} onChange={(event) => changeNumericFilter('min_width', event.target.value)} /></label>
          <label className="filter-field"><span>Maximum width (px)</span><input type="number" min="1" max="100000" step="1" value={numericFilters.max_width ?? ''} onChange={(event) => changeNumericFilter('max_width', event.target.value)} /></label>
          <label className="filter-field"><span>Minimum height (px)</span><input type="number" min="1" max="100000" step="1" value={numericFilters.min_height ?? ''} onChange={(event) => changeNumericFilter('min_height', event.target.value)} /></label>
          <label className="filter-field"><span>Maximum height (px)</span><input type="number" min="1" max="100000" step="1" value={numericFilters.max_height ?? ''} onChange={(event) => changeNumericFilter('max_height', event.target.value)} /></label>
          <label className="filter-field"><span>Minimum resolution (MP)</span><input type="number" min="0.1" max="10000" step="0.1" value={numericFilters.min_resolution_px === undefined ? '' : numericFilters.min_resolution_px / 1_000_000} onChange={(event) => changeNumericFilter('min_resolution_px', event.target.value, 1_000_000)} /></label>
          <label className="filter-field"><span>Maximum resolution (MP)</span><input type="number" min="0.1" max="10000" step="0.1" value={numericFilters.max_resolution_px === undefined ? '' : numericFilters.max_resolution_px / 1_000_000} onChange={(event) => changeNumericFilter('max_resolution_px', event.target.value, 1_000_000)} /></label>
          <label className="filter-field"><span>Minimum video duration (sec)</span><input type="number" min="0" max="10000000" step="0.1" value={numericFilters.min_duration_seconds ?? ''} onChange={(event) => changeNumericFilter('min_duration_seconds', event.target.value)} /></label>
          <label className="filter-field"><span>Maximum video duration (sec)</span><input type="number" min="0" max="10000000" step="0.1" value={numericFilters.max_duration_seconds ?? ''} onChange={(event) => changeNumericFilter('max_duration_seconds', event.target.value)} /></label>
          <label className="filter-field"><span>Minimum file size (MiB)</span><input type="number" min="0" max="8589934591" step="1" value={numericFilters.min_size_bytes === undefined ? '' : Math.round(numericFilters.min_size_bytes / (1024 * 1024))} onChange={(event) => changeNumericFilter('min_size_bytes', event.target.value, 1024 * 1024)} /></label>
          <label className="filter-field"><span>Maximum file size (MiB)</span><input type="number" min="0" max="8589934591" step="1" value={numericFilters.max_size_bytes === undefined ? '' : Math.round(numericFilters.max_size_bytes / (1024 * 1024))} onChange={(event) => changeNumericFilter('max_size_bytes', event.target.value, 1024 * 1024)} /></label>
        </div>
      </details>
    </div>
    {scan && <ScanProgress scan={scan} onCancel={onCancel} />}
    {error && <p className="app-error media-error" role="alert">{error}{items.length === 0 && <button className="text-button" onClick={() => setReloadToken((token) => token + 1)}>Retry</button>}</p>}
    {albumError && <p className="app-error media-error" role="alert">{albumError}</p>}
    {albumMessage && <p className="album-success" role="status">{albumMessage}</p>}
    {removeError && !confirmRemove && <p className="app-error media-error" role="alert">{removeError}</p>}
    {removeMessage && <p className="album-success" role="status">{removeMessage}</p>}
    {!loading && matchingCount > 0 && <div className="album-add-toolbar" aria-label="Media selection actions">
      <span>{selectedCount} selected{activeSelection.kind === 'all' ? ` of ${matchingCount} matching items` : ''}</span>
      {activeSelection.kind === 'all'
        ? null
        : <button className="quiet-button" onClick={() => setSelection(selectAllMedia(selectionScope))}>Select all {matchingCount} matching items</button>}
      {selectedCount > 0 && <>
        <label className="operation-check"><input type="checkbox" checked={organizeExports} onChange={(event) => setOrganizeExports(event.target.checked)} /><span>Organize Photos / Videos</span></label>
        <button className="quiet-button" onClick={() => void exportSelected()} disabled={transferBusy}>Export selected</button>
        {albums.length
          ? <>
            <label className="filter-field"><span>Add to album</span><select value={targetAlbumId} onChange={(event) => setTargetAlbumId(event.target.value)}>{albums.map((album) => <option key={album.id} value={album.id}>{album.name}</option>)}</select></label>
            <button className="quiet-button" onClick={addSelectedToAlbum}>Add to album</button>
          </>
          : <button className="quiet-button" onClick={onOpenAlbums}>Create an album</button>}
        <button className="quiet-button danger-button" onClick={() => setConfirmRemove(true)}>Remove from Library</button>
      </>}
      {(selectedCount > 0 || activeSelection.kind === 'all') &&
        <button className="text-button" onClick={() => setSelection(clearMediaSelection())}>Clear selection</button>}
    </div>}
    {loading
      ? <div className="media-loading" role="status">Loading {typeLabel.toLowerCase()}…</div>
      : items.length === 0 && !error
        ? <EmptyMedia type={mediaType} hasFilters={hasFilters} onClear={clearFilters} onAddMedia={onAddMedia} scanRunning={scanRunning} />
        : <div className="media-grid">
          {items.map((item) => <MediaCard
            key={item.id}
            item={item}
            selected={isMediaSelected(activeSelection, item.id, selectionScope)}
            onToggle={toggleSelection}
            onOpen={openPhoto}
          />)}
        </div>}
    {!loading && hasMore && <div className="load-more-container">
      <button className="quiet-button load-more-button" onClick={() => void loadMore()} disabled={loadingMore}>
        {loadingMore ? 'Loading more…' : 'Load more'}
      </button>
    </div>}
    {confirmRemove && <div className="photo-viewer-backdrop" role="presentation" onClick={() => !removing && setConfirmRemove(false)}>
      <section className="bulk-remove-dialog" role="dialog" aria-modal="true" aria-labelledby="remove-media-title" aria-describedby="remove-media-description" onClick={(event) => event.stopPropagation()}>
        <h2 id="remove-media-title">Remove {selectedCount} {selectedCount === 1 ? 'item' : 'items'} from the library?</h2>
        <p id="remove-media-description">This removes PhotoFlow records and cached thumbnails only. Original media files will not be deleted, moved, or modified. Album memberships will be removed; occasion groups update automatically.</p>
        {removeError && <p className="app-error" role="alert">{removeError}</p>}
        <div className="album-actions">
          <button className="quiet-button" onClick={() => setConfirmRemove(false)} disabled={removing} autoFocus>Cancel</button>
          <button className="quiet-button danger-button" onClick={() => void removeSelected()} disabled={removing}>
            {removing ? 'Removing…' : 'Remove from Library'}
          </button>
        </div>
      </section>
    </div>}
    {viewer && <div className="photo-viewer-backdrop" role="presentation" onClick={() => setViewer(undefined)}>
      <section className="photo-viewer" role="dialog" aria-modal="true" aria-label={`Full-resolution photo ${viewer.item.filename}`} onClick={(event) => event.stopPropagation()}>
        <header><div><strong>{viewer.item.filename}</strong><p>{viewer.item.width && viewer.item.height ? `${viewer.item.width} × ${viewer.item.height}` : 'Dimensions unavailable'}</p></div><button className="quiet-button" onClick={() => setViewer(undefined)} aria-label="Close photo">Close</button></header>
        <div className="photo-viewer-content">
          {viewer.loading
            ? <p role="status">Opening full-resolution photo…</p>
            : viewer.error
              ? <p className="app-error" role="alert">{viewer.error}</p>
              : viewer.dataUrl && <img src={viewer.dataUrl} alt={viewer.item.filename} />}
        </div>
      </section>
    </div>}
    {saveSearchOpen && <div className="photo-viewer-backdrop" role="presentation" onClick={() => setSaveSearchOpen(false)}>
      <section className="bulk-remove-dialog save-smart-search-dialog" role="dialog" aria-modal="true" aria-labelledby="save-search-title" onClick={(event) => event.stopPropagation()}>
        <h2 id="save-search-title">Save search as a Smart Album</h2>
        <p>Current media type and filters are saved as a live collection. Its results update as your library changes.</p>
        {savedSearchError && <p className="app-error" role="alert">{savedSearchError}</p>}
        <form onSubmit={(event) => void saveSearch(event)}>
          <label className="filter-field"><span>Collection name</span><input autoFocus maxLength={100} value={savedSearchName} onChange={(event) => setSavedSearchName(event.target.value)} required /></label>
          <label className="filter-field"><span>Description (optional)</span><input maxLength={500} value={savedSearchDescription} onChange={(event) => setSavedSearchDescription(event.target.value)} /></label>
          <div className="album-actions">
            <button type="button" className="quiet-button" onClick={() => setSaveSearchOpen(false)}>Cancel</button>
            <button type="submit" className="primary-button">Save Smart Album</button>
          </div>
        </form>
      </section>
    </div>}
  </section>
}

const MediaCard = memo(function MediaCard({
  item,
  selected,
  onToggle,
  onOpen
}: {
  item: MediaItem
  selected: boolean
  onToggle: (mediaId: number, checked: boolean) => void
  onOpen: (item: MediaItem) => void
}) {
  return <article className={`media-card${selected ? ' selected' : ''}`}>
    <div className="media-preview">
      <label className="media-select">
        <input
          type="checkbox"
          checked={selected}
          aria-label={`Select ${item.filename}`}
          onChange={(event) => onToggle(item.id, event.target.checked)}
        />
      </label>
      {item.media_type === 'photo'
        ? <button className="media-open-button" onClick={() => void onOpen(item)} aria-label={`Open full-resolution ${item.filename}`}>
          <MediaThumbnail mediaId={item.id} mediaType={item.media_type} />
        </button>
        : <MediaThumbnail mediaId={item.id} mediaType={item.media_type} />}
      {item.media_type === 'video' && <b className="duration">{formatDuration(item.duration_seconds)}</b>}
    </div>
    <div className="media-card-copy">
      <strong title={item.filename}>{item.filename}</strong>
      <p>{item.width && item.height ? `${item.width} × ${item.height}` : 'Dimensions unavailable'} <span>•</span> {formatBytes(item.size_bytes)}</p>
      <p className="metadata-status">{metadataStatusLabel(item)}</p>
      <p className="metadata-detail">Captured: {item.capture_date ? formatCaptureDate(item.capture_date) : 'unavailable'}</p>
      <p className="metadata-detail">Camera: {cameraLabel(item) || 'unavailable'}</p>
    </div>
  </article>
})

function EmptyMedia({ type, hasFilters, onClear, onAddMedia, scanRunning }: { type: 'all' | 'photo' | 'video'; hasFilters: boolean; onClear: () => void; onAddMedia: () => void; scanRunning: boolean }) {
  const label = type === 'all' ? 'media' : type === 'photo' ? 'photos' : 'videos'
  return <div className="empty-media">
    <div><Icon name={type === 'video' ? 'video' : 'image'} size={31} /></div>
    <h2>{hasFilters ? 'No matching media' : `No ${label} yet`}</h2>
    <p>{hasFilters ? 'Try changing your search or filters to see more results.' : 'Select a local folder to scan it safely. PhotoFlow only reads original files and saves its database and previews separately.'}</p>
    {hasFilters
      ? <button className="primary-button" onClick={onClear}>Clear filters</button>
      : <button className="primary-button" onClick={onAddMedia} disabled={scanRunning}>Select folders</button>}
  </div>
}

function errorMessage(error: unknown, fallback: string): string { return error instanceof Error ? error.message : fallback }
function formatCaptureDate(value: string): string {
  return value.replace(/^(\d{4}):(\d{2}):(\d{2})/, '$1-$2-$3')
}
function cameraLabel(item: MediaItem): string {
  return [item.camera_make, item.camera_model].filter((value): value is string => Boolean(value?.trim())).join(' ')
}
function formatBytes(value: number) { return value > 1_000_000 ? `${(value / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1_000))} KB` }
function formatDuration(value?: number | null) { if (!value) return 'Video'; const minutes = Math.floor(value / 60); return `${minutes}:${Math.floor(value % 60).toString().padStart(2, '0')}` }
