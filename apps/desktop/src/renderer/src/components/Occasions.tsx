import { useEffect, useRef, useState } from 'react'
import { CollectionMediaGrid } from './CollectionMediaGrid'
import { Icon } from './Icon'

const OCCASION_PAGE_SIZE = 100
const MEDIA_PAGE_SIZE = 60

export function Occasions() {
  const [groupBy, setGroupBy] = useState<OccasionGrouping>('month')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [occasions, setOccasions] = useState<Occasion[]>([])
  const [selected, setSelected] = useState<Occasion>()
  const [items, setItems] = useState<MediaItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingItems, setLoadingItems] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string>()
  const offsetRef = useRef(0)
  const itemOffsetRef = useRef(0)
  const requestId = useRef(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(undefined)
    setSelected(undefined)
    setItems([])
    offsetRef.current = 0
    void window.photoFlow.listOccasions(groupBy, dateFrom || undefined, dateTo || undefined, OCCASION_PAGE_SIZE, 0).then((result) => {
      if (!active) return
      setOccasions(result)
      offsetRef.current = result.length
      setHasMore(result.length === OCCASION_PAGE_SIZE)
    }).catch((loadError: unknown) => {
      if (active) {
        setOccasions([])
        setError(errorMessage(loadError, 'Could not load occasions.'))
      }
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [groupBy, dateFrom, dateTo])

  useEffect(() => {
    if (!selected) return
    const request = ++requestId.current
    setLoadingItems(true)
    setItems([])
    itemOffsetRef.current = 0
    setError(undefined)
    void window.photoFlow.listOccasionMedia(groupBy, selected.period, selected.date_source, MEDIA_PAGE_SIZE, 0).then((result) => {
      if (request !== requestId.current) return
      setItems(result)
      itemOffsetRef.current = result.length
      setHasMore(result.length === MEDIA_PAGE_SIZE)
    }).catch((loadError: unknown) => {
      if (request === requestId.current) setError(errorMessage(loadError, 'Could not open this occasion.'))
    }).finally(() => { if (request === requestId.current) setLoadingItems(false) })
    return () => { requestId.current += 1 }
  }, [groupBy, selected])

  const loadMoreOccasions = async (): Promise<void> => {
    setLoadingMore(true)
    setError(undefined)
    try {
      const result = await window.photoFlow.listOccasions(
        groupBy, dateFrom || undefined, dateTo || undefined, OCCASION_PAGE_SIZE, offsetRef.current
      )
      setOccasions((current) => [...current, ...result])
      offsetRef.current += result.length
      setHasMore(result.length === OCCASION_PAGE_SIZE)
    } catch (loadError) {
      setError(errorMessage(loadError, 'Could not load more occasions.'))
    } finally {
      setLoadingMore(false)
    }
  }

  const loadMoreMedia = async (): Promise<void> => {
    if (!selected) return
    setLoadingMore(true)
    setError(undefined)
    try {
      const result = await window.photoFlow.listOccasionMedia(
        groupBy, selected.period, selected.date_source, MEDIA_PAGE_SIZE, itemOffsetRef.current
      )
      setItems((current) => [...current, ...result])
      itemOffsetRef.current += result.length
      setHasMore(result.length === MEDIA_PAGE_SIZE)
    } catch (loadError) {
      setError(errorMessage(loadError, 'Could not load more media.'))
    } finally {
      setLoadingMore(false)
    }
  }

  const grouped = groupOccasions(occasions)

  return <section className="collections-page occasions-page">
    <header className="collections-header">
      <div><p className="section-kicker">MEMORIES IN TIME</p><h1>{selected ? formatPeriod(selected.period, groupBy) : 'Occasions'}</h1>
        <p>{selected ? <>{dateSourceLabel(selected.date_source)} · {selected.media_count} {selected.media_count === 1 ? 'item' : 'items'}</> : 'Browse indexed media by capture date, or by import date when capture metadata is unavailable.'}</p>
      </div>
      {selected && <button className="text-button" onClick={() => { requestId.current += 1; setSelected(undefined); setItems([]); setError(undefined) }}>← All occasions</button>}
    </header>

    {error && <p className="app-error collection-error" role="alert">{error}</p>}
    {!selected && <div className="occasion-controls">
      <label className="filter-field"><span>Group by</span><select value={groupBy} onChange={(event) => setGroupBy(event.target.value as OccasionGrouping)}>
        <option value="year">Year</option><option value="month">Month</option><option value="day">Capture date</option>
      </select></label>
      <label className="filter-field"><span>From</span><input type="date" value={dateFrom} max={dateTo || undefined} onChange={(event) => setDateFrom(event.target.value)} /></label>
      <label className="filter-field"><span>To</span><input type="date" value={dateTo} min={dateFrom || undefined} onChange={(event) => setDateTo(event.target.value)} /></label>
      {(dateFrom || dateTo) && <button className="text-button" onClick={() => { setDateFrom(''); setDateTo('') }}>Clear date range</button>}
    </div>}

    {selected
      ? loadingItems
        ? <div className="collection-empty" role="status">Loading occasion…</div>
        : <><p className={`occasion-date-source ${selected.date_source}`}>{selected.date_source === 'captured'
          ? 'Dates come from indexed capture metadata.'
          : 'Capture date unavailable for these files. Grouped by the date PhotoFlow indexed them.'}</p>
        <CollectionMediaGrid items={items} emptyMessage="No media is available for this occasion." />
        {hasMore && <div className="load-more-container"><button className="quiet-button" onClick={() => void loadMoreMedia()} disabled={loadingMore}>{loadingMore ? 'Loading more…' : 'Load more'}</button></div>}</>
      : loading
        ? <div className="collection-empty" role="status">Loading occasions…</div>
        : occasions.length === 0
          ? <div className="collection-empty"><Icon name="calendar" size={30} /><h2>No dated media yet</h2><p>Capture dates are taken from media metadata. When unavailable, indexed dates are shown separately.</p></div>
          : <div className="occasion-timeline">{grouped.map((yearGroup) => <section className="occasion-year" key={yearGroup.year}>
            <h2>{yearGroup.year}</h2><div className="occasion-list">{yearGroup.items.map((occasion) =>
              <OccasionTile key={`${occasion.period}:${occasion.date_source}`} occasion={occasion} groupBy={groupBy} onClick={() => setSelected(occasion)} />
            )}</div>
          </section>)}</div>}
    {!selected && !loading && hasMore && <div className="load-more-container"><button className="quiet-button" onClick={() => void loadMoreOccasions()} disabled={loadingMore}>{loadingMore ? 'Loading more…' : 'Load more occasions'}</button></div>}
  </section>
}

function OccasionTile({ occasion, groupBy, onClick }: { occasion: Occasion; groupBy: OccasionGrouping; onClick: () => void }) {
  const [cover, setCover] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    void window.photoFlow.getThumbnail(occasion.cover_media_id).then((value) => { if (active) setCover(value) }).catch(() => { if (active) setCover(null) })
    return () => { active = false }
  }, [occasion.cover_media_id])
  return <button className="occasion-card" onClick={onClick}>
    <div className="occasion-cover">{cover ? <img src={cover} alt="" /> : <Icon name="calendar" size={26} />}</div>
    <div className="occasion-card-copy"><strong>{formatPeriod(occasion.period, groupBy)}</strong>
      <span className={`date-source-tag ${occasion.date_source}`}>{occasion.date_source === 'captured' ? 'Capture date' : 'Indexed date · no capture date'}</span>
      <small>{occasion.media_count} {occasion.media_count === 1 ? 'item' : 'items'}</small>
    </div><Icon name="arrow-up" size={15} />
  </button>
}

function groupOccasions(occasions: Occasion[]): Array<{ year: string; items: Occasion[] }> {
  const groups = new Map<string, Occasion[]>()
  for (const occasion of occasions) {
    const year = occasion.period.slice(0, 4)
    const items = groups.get(year) ?? []
    items.push(occasion)
    groups.set(year, items)
  }
  return [...groups.entries()].map(([year, items]) => ({ year, items }))
}

function formatPeriod(value: string, groupBy: OccasionGrouping): string {
  if (groupBy === 'year') return value
  const date = new Date(`${groupBy === 'month' ? `${value}-01` : value}T00:00:00`)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, {
    year: 'numeric',
    ...(groupBy === 'month' ? { month: 'long' } : { month: 'long', day: 'numeric' })
  })
}

function dateSourceLabel(source: Occasion['date_source']): string {
  return source === 'captured' ? 'Capture date' : 'Indexed date · capture date unavailable'
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
