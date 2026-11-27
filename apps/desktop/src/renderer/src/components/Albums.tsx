import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { CollectionMediaGrid } from './CollectionMediaGrid'
import { Icon } from './Icon'

type AlbumView = { type: 'manual'; album: Album } | { type: 'smart'; album: SmartAlbum }

const PAGE_SIZE = 60

export function Albums({ onTransferStarted, transferBusy }: { onTransferStarted: (job: TransferJob) => void; transferBusy: boolean }) {
  const [view, setView] = useState<'manual' | 'smart'>('manual')
  const [albums, setAlbums] = useState<Album[]>([])
  const [smartAlbums, setSmartAlbums] = useState<SmartAlbum[]>([])
  const [selected, setSelected] = useState<AlbumView>()
  const [items, setItems] = useState<MediaItem[]>([])
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadingItems, setLoadingItems] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string>()
  const [message, setMessage] = useState<string>()
  const [organizeExports, setOrganizeExports] = useState(true)
  const offsetRef = useRef(0)
  const requestId = useRef(0)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(undefined)
    try {
      const [manual, smart] = await Promise.all([
        window.photoFlow.listAlbums(),
        window.photoFlow.listSmartAlbums()
      ])
      setAlbums(manual)
      setSmartAlbums(smart)
    } catch (loadError) {
      setError(errorMessage(loadError, 'Could not load albums.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const fetchItems = async (target: AlbumView, offset: number, append: boolean): Promise<void> => {
    const request = ++requestId.current
    if (append) setLoadingMore(true)
    else {
      setLoadingItems(true)
      setItems([])
      setError(undefined)
      offsetRef.current = 0
    }
    try {
      const result = target.type === 'manual'
        ? await window.photoFlow.listAlbumMedia(target.album.id, PAGE_SIZE, offset)
        : await window.photoFlow.listSmartAlbumMedia(target.album.kind, target.album.value, PAGE_SIZE, offset)
      if (request !== requestId.current) return
      setItems((current) => append ? [...current, ...result] : result)
      offsetRef.current = offset + result.length
      setHasMore(result.length === PAGE_SIZE)
    } catch (loadError) {
      if (request === requestId.current) setError(errorMessage(loadError, 'Could not load this collection.'))
    } finally {
      if (request === requestId.current) {
        setLoadingItems(false)
        setLoadingMore(false)
      }
    }
  }

  const openManual = (album: Album): void => {
    const target: AlbumView = { type: 'manual', album }
    setSelected(target)
    setName(album.name)
    setDescription(album.description)
    setEditing(false)
    setConfirmDelete(false)
    void fetchItems(target, 0, false)
  }

  const openSmart = (album: SmartAlbum): void => {
    const target: AlbumView = { type: 'smart', album }
    setSelected(target)
    setEditing(false)
    void fetchItems(target, 0, false)
  }

  const submitAlbum = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setError(undefined)
    setMessage(undefined)
    try {
      if (editing && selected?.type === 'manual') {
        const updated = await window.photoFlow.renameAlbum(selected.album.id, name, description)
        setSelected({ type: 'manual', album: updated })
        setEditing(false)
        setMessage('Album updated.')
      } else {
        const created = await window.photoFlow.createAlbum(name, description)
        setSelected({ type: 'manual', album: created })
        setItems([])
        setHasMore(false)
        setCreating(false)
        setMessage('Album created. Add items from Photos or Videos.')
        setName('')
        setDescription('')
      }
      await refresh()
    } catch (saveError) {
      setError(errorMessage(saveError, 'Could not save this album.'))
    }
  }

  const removeFromAlbum = async (mediaId: number): Promise<void> => {
    if (selected?.type !== 'manual') return
    setError(undefined)
    try {
      await window.photoFlow.removeAlbumMedia(selected.album.id, [mediaId])
      const updated = { ...selected.album, media_count: Math.max(0, selected.album.media_count - 1) }
      setSelected({ type: 'manual', album: updated })
      setItems((current) => current.filter((item) => item.id !== mediaId))
      await refresh()
    } catch (removeError) {
      setError(errorMessage(removeError, 'Could not remove this item from the album.'))
    }
  }

  const removeAlbum = async (): Promise<void> => {
    if (selected?.type !== 'manual') return
    setError(undefined)
    try {
      await window.photoFlow.deleteAlbum(selected.album.id)
      setSelected(undefined)
      setItems([])
      setConfirmDelete(false)
      setMessage('Album deleted. The media remains in your library.')
      await refresh()
    } catch (deleteError) {
      setError(errorMessage(deleteError, 'Could not delete this album.'))
    }
  }

  const exportAlbum = async (): Promise<void> => {
    if (selected?.type !== 'manual') return
    setError(undefined)
    try {
      const result = await window.photoFlow.exportAlbum(selected.album.id, organizeExports)
      if (result.job) onTransferStarted(result.job)
    } catch (exportError) {
      setError(errorMessage(exportError, 'Could not start album export.'))
    }
  }

  const loadMore = (): void => {
    if (selected && !loadingMore) void fetchItems(selected, offsetRef.current, true)
  }

  return <section className="collections-page">
    <header className="collections-header">
      <div><p className="section-kicker">ORGANIZE YOUR LIBRARY</p><h1>Albums</h1><p>Keep related photos and videos together. Albums never change your original files.</p></div>
      {!selected && view === 'manual' && <button className="primary-button" onClick={() => { setCreating((current) => !current); setName(''); setDescription('') }}><Icon name="plus" size={17} /> New album</button>}
      {selected && <button className="text-button" onClick={() => { requestId.current += 1; setSelected(undefined); setItems([]); setError(undefined) }}>← All albums</button>}
    </header>

    {error && <p className="app-error collection-error" role="alert">{error}</p>}
    {message && <p className="album-success" role="status">{message}</p>}
    {!selected && <div className="collection-tabs" role="tablist" aria-label="Album collections">
      <button role="tab" aria-selected={view === 'manual'} className={view === 'manual' ? 'active' : ''} onClick={() => { setView('manual'); setCreating(false); setError(undefined) }}>My albums <span>{albums.length}</span></button>
      <button role="tab" aria-selected={view === 'smart'} className={view === 'smart' ? 'active' : ''} onClick={() => { setView('smart'); setCreating(false); setError(undefined) }}>Smart albums <span>Live</span></button>
    </div>}

    {creating && !selected && <form className="album-form" onSubmit={(event) => void submitAlbum(event)}>
      <label>Album name<input autoFocus maxLength={100} value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Summer trip" required /></label>
      <label>Description <span>Optional</span><input maxLength={500} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Add a note about this collection" /></label>
      <div><button className="primary-button" type="submit">Create album</button><button type="button" className="text-button" onClick={() => setCreating(false)}>Cancel</button></div>
    </form>}

    {selected
      ? <section className="album-detail">
        <div className="album-detail-heading">
          <div><p className="section-kicker">{selected.type === 'manual' ? 'MANUAL ALBUM' : 'SMART ALBUM · UPDATES AUTOMATICALLY'}</p><h2>{selected.album.name}</h2><p>{selected.type === 'manual' ? selected.album.description || 'Add photos and videos from your library.' : smartDescription(selected.album)}</p></div>
          <span className="collection-count">{selected.album.media_count} items</span>
        </div>
        {selected.type === 'manual' && editing && <form className="album-form inline" onSubmit={(event) => void submitAlbum(event)}>
          <label>Album name<input autoFocus maxLength={100} value={name} onChange={(event) => setName(event.target.value)} required /></label>
          <label>Description <span>Optional</span><input maxLength={500} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
          <div><button className="primary-button" type="submit">Save changes</button><button type="button" className="text-button" onClick={() => setEditing(false)}>Cancel</button></div>
        </form>}
        {selected.type === 'manual' && !editing && <div className="album-actions">
          <label className="operation-check"><input type="checkbox" checked={organizeExports} onChange={(event) => setOrganizeExports(event.target.checked)} /><span>Organize Photos / Videos</span></label>
          <button className="quiet-button" onClick={() => void exportAlbum()} disabled={transferBusy || selected.album.media_count === 0}>Export album</button>
          <button className="quiet-button" onClick={() => setEditing(true)}>Rename / edit</button>
          <button className="quiet-button danger-button" onClick={() => setConfirmDelete(true)}>Delete album</button>
        </div>}
        {confirmDelete && <div className="delete-confirm" role="alert">
          <span>Delete this album and its memberships? Media stays in your library.</span>
          <button className="quiet-button danger-button" onClick={() => void removeAlbum()}>Delete album</button>
          <button className="text-button" onClick={() => setConfirmDelete(false)}>Cancel</button>
        </div>}
        {loadingItems ? <div className="collection-empty" role="status">Loading items…</div>
          : <CollectionMediaGrid items={items} emptyMessage={selected.type === 'manual' ? 'This album is empty. Select media in Photos or Videos and add it here.' : 'No media currently matches this smart album.'} onRemove={selected.type === 'manual' ? (id) => void removeFromAlbum(id) : undefined} />}
        {hasMore && <div className="load-more-container"><button className="quiet-button" onClick={loadMore} disabled={loadingMore}>{loadingMore ? 'Loading more…' : 'Load more'}</button></div>}
      </section>
      : loading
        ? <div className="collection-empty" role="status">Loading collections…</div>
        : view === 'manual'
          ? albums.length
            ? <div className="album-grid">{albums.map((album) => <AlbumTile key={album.id} name={album.name} detail={album.description} count={album.media_count} coverId={album.cover_media_id} onClick={() => openManual(album)} />)}</div>
            : !creating && <div className="collection-empty"><Icon name="folder" size={30} /><h2>No albums yet</h2><p>Create an album, then add media from Photos or Videos.</p><button className="primary-button" onClick={() => setCreating(true)}><Icon name="plus" size={16} /> Create your first album</button></div>
          : smartAlbums.length
            ? <div className="album-grid">{smartAlbums.map((album) => <AlbumTile key={`${album.kind}:${album.value}`} name={album.name} detail={smartDescription(album)} count={album.media_count} coverId={album.cover_media_id} onClick={() => openSmart(album)} smart />)}</div>
            : <div className="collection-empty"><Icon name="sparkle" size={30} /><h2>Smart albums appear as your library grows</h2><p>Capture dates, camera metadata, media type, and recent imports automatically shape these collections.</p></div>}
  </section>
}

function AlbumTile({ name, detail, count, coverId, onClick, smart = false }: { name: string; detail: string; count: number; coverId: number | null; onClick: () => void; smart?: boolean }) {
  const [cover, setCover] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    if (coverId !== null) void window.photoFlow.getThumbnail(coverId).then((value) => { if (active) setCover(value) }).catch(() => { if (active) setCover(null) })
    else setCover(null)
    return () => { active = false }
  }, [coverId])
  return <button className="album-tile" onClick={onClick}>
    <div className="album-cover">{cover ? <img src={cover} alt="" /> : <Icon name={smart ? 'sparkle' : 'folder'} size={31} />}{smart && <span className="live-tag">LIVE</span>}</div>
    <div className="album-tile-copy"><strong>{name}</strong><p>{detail || 'Manual collection'}</p><span>{count} {count === 1 ? 'item' : 'items'}</span></div>
  </button>
}

function smartDescription(album: SmartAlbum): string {
  if (album.kind === 'year') return `Captured in ${album.value}`
  if (album.kind === 'month') return `Captured in ${formatMonth(album.value)}`
  if (album.kind === 'camera') return 'Grouped by camera metadata'
  if (album.kind === 'media_type') return album.value === 'photo' ? 'All indexed photos' : 'All indexed videos'
  if (album.kind === 'recent') return 'Indexed in the last 30 days'
  return 'All indexed videos'
}

function formatMonth(value: string): string {
  const date = new Date(`${value}-01T00:00:00`)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
