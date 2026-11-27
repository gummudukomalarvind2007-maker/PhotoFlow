import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'
import { MediaThumbnail } from './MediaThumbnail'

const PAGE_SIZE = 20
const CATEGORIES: Array<{ id: PhotoReviewCategory; label: string; detail: string }> = [
  { id: 'exact', label: 'Exact Duplicates', detail: 'Identical SHA-256 content' },
  { id: 'similar', label: 'Similar Photos', detail: 'Perceptual similarity' },
  { id: 'blurry', label: 'Blurry Photos', detail: 'Low Laplacian variance' },
  { id: 'low_resolution', label: 'Low Resolution', detail: 'Small image dimensions' },
  { id: 'exposure', label: 'Dark or Overexposed', detail: 'Brightness + pixel distribution' }
]

interface QualityReviewProps { initialCategory?: PhotoReviewCategory }

export function QualityReview({ initialCategory = 'blurry' }: QualityReviewProps) {
  const [category, setCategory] = useState<PhotoReviewCategory>(initialCategory)
  const [status, setStatus] = useState<PhotoReviewStatus>('pending')
  const [threshold, setThreshold] = useState(6)
  const [job, setJob] = useState<PhotoReviewJob | null>(null)
  const [overview, setOverview] = useState<Record<PhotoReviewCategory, number>>({ exact: 0, similar: 0, blurry: 0, low_resolution: 0, exposure: 0 })
  const [result, setResult] = useState<PhotoReviewResult>({ groups: [], items: [] })
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string>()
  const [busyItems, setBusyItems] = useState<Set<number>>(new Set())
  const [refresh, setRefresh] = useState(0)
  const offsetRef = useRef(0)
  const requestId = useRef(0)
  const running = job?.status === 'running'
  const displayGroups = category === 'exact' || category === 'similar'
  const pageItems = displayGroups ? result.groups.flatMap((group) => group.items) : result.items

  useEffect(() => setCategory(initialCategory), [initialCategory])
  const reloadOverview = useCallback(async () => {
    try {
      const current = await window.photoFlow.getPhotoReviewOverview(threshold)
      setOverview(current)
    } catch (loadError) {
      setError(message(loadError, 'Could not load review counts.'))
    }
  }, [threshold])

  useEffect(() => {
    let active = true
    void window.photoFlow.getPhotoReviewStatus().then((current) => { if (active) setJob(current) }).catch((loadError: unknown) => {
      if (active) setError(message(loadError, 'Could not load analysis status.'))
    })
    void reloadOverview()
    return () => { active = false }
  }, [reloadOverview])

  useEffect(() => {
    if (!running || !job) return
    const timer = window.setInterval(() => {
      void window.photoFlow.getPhotoReviewStatus(job.id).then((current) => {
        if (current) setJob(current)
      }).catch((loadError: unknown) => setError(message(loadError, 'Could not refresh analysis progress.')))
    }, 1_500)
    return () => window.clearInterval(timer)
  }, [running, job?.id])

  const reloadPage = useCallback(async () => {
    const currentRequest = ++requestId.current
    offsetRef.current = 0
    setLoading(true)
    setError(undefined)
    try {
      const page = await window.photoFlow.listPhotoReview(category, status, PAGE_SIZE, 0, threshold)
      if (currentRequest !== requestId.current) return
      setResult(page)
      offsetRef.current = displayGroups ? page.groups.length : page.items.length
      setHasMore((displayGroups ? page.groups.length : page.items.length) === PAGE_SIZE)
    } catch (loadError) {
      if (currentRequest === requestId.current) {
        setResult({ groups: [], items: [] })
        setHasMore(false)
        setError(message(loadError, 'Could not load review items.'))
      }
    } finally {
      if (currentRequest === requestId.current) setLoading(false)
    }
  }, [category, status, threshold, displayGroups])

  useEffect(() => { void reloadPage() }, [reloadPage, refresh])
  useEffect(() => {
    if (job?.status === 'completed' || job?.status === 'failed' || job?.status === 'cancelled') {
      void reloadOverview()
      setRefresh((current) => current + 1)
    }
  }, [job?.status, reloadOverview])

  const start = async (): Promise<void> => {
    setError(undefined)
    try {
      const started = await window.photoFlow.startPhotoReview(threshold)
      setJob(started)
    } catch (startError) {
      setError(message(startError, 'Could not start photo analysis.'))
    }
  }

  const cancel = async (): Promise<void> => {
    if (!job) return
    setError(undefined)
    try { setJob(await window.photoFlow.cancelPhotoReview(job.id)) }
    catch (cancelError) { setError(message(cancelError, 'Could not cancel photo analysis.')) }
  }

  const mark = useCallback(async (mediaId: number, next: PhotoReviewMark): Promise<void> => {
    setBusyItems((current) => new Set(current).add(mediaId))
    setError(undefined)
    try {
      await window.photoFlow.setPhotoReviewStatus(mediaId, next)
      await reloadOverview()
      setRefresh((current) => current + 1)
    } catch (markError) {
      setError(message(markError, 'Could not save this review status.'))
    } finally {
      setBusyItems((current) => {
        const updated = new Set(current)
        updated.delete(mediaId)
        return updated
      })
    }
  }, [reloadOverview])

  const loadMore = async (): Promise<void> => {
    if (loadingMore || !hasMore) return
    setLoadingMore(true)
    setError(undefined)
    const currentRequest = requestId.current
    try {
      const page = await window.photoFlow.listPhotoReview(category, status, PAGE_SIZE, offsetRef.current, threshold)
      if (currentRequest !== requestId.current) return
      const incomingItems = pageItemsFor(page, category)
      if (displayGroups) {
        const currentIds = new Set(result.groups.map((group) => group.id))
        setResult((current) => ({ ...current, groups: [...current.groups, ...page.groups.filter((group) => !currentIds.has(group.id))] }))
        offsetRef.current += page.groups.length
      } else {
        const currentIds = new Set(result.items.map((item) => item.id))
        setResult((current) => ({ ...current, items: [...current.items, ...page.items.filter((item) => !currentIds.has(item.id))] }))
        offsetRef.current += page.items.length
      }
      setHasMore(incomingItems.length === PAGE_SIZE)
    } catch (loadError) {
      if (currentRequest === requestId.current) setError(message(loadError, 'Could not load more review items.'))
    } finally {
      setLoadingMore(false)
    }
  }

  const selectedCategory = CATEGORIES.find((item) => item.id === category)!
  return <section className="quality-review">
    <header className="review-header">
      <div>
        <p className="section-kicker">PHOTO FLOW · REVIEW</p>
        <h1>Quality Review</h1>
        <p>These are review suggestions, not automatic judgments. PhotoFlow never changes or deletes originals.</p>
      </div>
      <div className="review-run-controls">
        <label className="filter-field">
          <span>Similar-photo threshold</span>
          <select value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} disabled={running}>
            {[3, 4, 5, 6, 7].map((value) => <option key={value} value={value}>Distance ≤ {value}/64</option>)}
          </select>
        </label>
        {running
          ? <button className="scan-cancel" onClick={() => void cancel()}>Cancel analysis</button>
          : <button className="primary-button" onClick={() => void start()}><Icon name="sparkle" size={16} /> Analyze photos</button>}
      </div>
    </header>

    {job && <section className="review-job" aria-live="polite">
      <div className="review-job-heading"><strong>{job.status === 'running' ? 'Analyzing local photos' : `Analysis ${job.status}`}</strong><span>{job.processed_files} / {job.total_files} checked · {job.analyzed_files} analyzed · {job.failed_files} unavailable</span></div>
      <div className={`progress${job.status === 'running' && job.processed_files === 0 ? ' discovering' : ''}`}>
        <span style={{ width: job.status === 'running' && job.total_files === 0 ? '28%' : `${job.total_files ? Math.min(100, Math.round(job.processed_files * 100 / job.total_files)) : job.status === 'completed' ? 100 : 0}%` }} />
      </div>
      {job.error_summary && <p className="review-error-detail">{job.error_summary}</p>}
    </section>}

    <div className="review-layout">
      <nav className="review-categories" aria-label="Review categories">
        {CATEGORIES.map((item) => <button key={item.id} className={category === item.id ? 'review-category active' : 'review-category'} aria-current={category === item.id ? 'page' : undefined} onClick={() => setCategory(item.id)}>
          <span><strong>{item.label}</strong><small>{item.detail}</small></span><b>{overview[item.id]}</b>
        </button>)}
      </nav>
      <div className="review-results">
        <div className="review-results-header">
          <div><p className="section-kicker">{selectedCategory.label.toUpperCase()}</p><h2>{selectedCategory.detail}</h2></div>
          <label className="filter-field review-status-select"><span>Show</span>
            <select value={status} onChange={(event) => setStatus(event.target.value as PhotoReviewStatus)}>
              <option value="pending">Needs review</option><option value="later">Keep for later</option><option value="reviewed">Reviewed</option><option value="all">All statuses</option>
            </select>
          </label>
        </div>
        {category === 'exact' && <p className="review-explanation">Exact duplicates have byte-for-byte identical SHA-256 hashes. Similar Photos are visually alike but can differ in content or format.</p>}
        {category === 'similar' && <p className="review-explanation">Perceptual hashes compare visual structure. Similar photos are not exact duplicates; threshold controls the maximum 64-bit Hamming distance.</p>}
        {category !== 'exact' && category !== 'similar' && job?.status !== 'completed' && job?.status !== 'running' && <p className="review-explanation">Run Analyze photos to inspect indexed images. Review flags are independent suggestions, never an automatic “bad photo” decision.</p>}
        {error && <p className="app-error review-error" role="alert">{error}<button className="text-button" onClick={() => setRefresh((current) => current + 1)}>Retry</button></p>}
        {loading
          ? <div className="media-loading" role="status">Loading review items…</div>
          : displayGroups
            ? <div className="review-group-list">{result.groups.map((group) => <article className="review-group" key={group.id}>
              <div className="review-group-heading"><strong>{category === 'exact' ? `Exact duplicate group · ${group.item_count} files` : `Similar photo pair · distance ${group.distance}/64`}</strong><p>{group.explanation}</p></div>
              <div className="review-item-grid">{group.items.map((item) => <ReviewCard key={item.id} item={item} busy={busyItems.has(item.id)} onMark={mark} />)}</div>
            </article>)}</div>
            : <div className="review-item-grid">{result.items.map((item) => <ReviewCard key={item.id} item={item} busy={busyItems.has(item.id)} onMark={mark} />)}</div>}
        {!loading && pageItems.length === 0 && !error && <div className="empty-media review-empty"><div><Icon name="sparkle" size={28} /></div><h2>{status === 'pending' ? 'Nothing to review yet' : 'No items in this view'}</h2><p>{job?.status !== 'completed' && job?.status !== 'running' ? 'Analyze your indexed photos to find duplicates and quality-review candidates.' : 'There are no items matching this review category and status.'}</p>{job?.status !== 'running' && <button className="primary-button" onClick={() => void start()}>Analyze photos</button>}</div>}
        {!loading && hasMore && <div className="load-more-container"><button className="quiet-button load-more-button" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? 'Loading more…' : 'Load more'}</button></div>}
      </div>
    </div>
  </section>
}

const ReviewCard = memo(function ReviewCard({ item, busy, onMark }: { item: ReviewMediaItem; busy: boolean; onMark: (mediaId: number, status: PhotoReviewMark) => Promise<void> }) {
  return <article className="review-card">
    <div className="review-preview"><MediaThumbnail mediaId={item.id} mediaType={item.media_type} /></div>
    <div className="review-card-content">
      <strong title={item.filename}>{item.filename}{item.is_representative && <span className="representative-tag">Representative</span>}</strong>
      <p>{item.extension.toUpperCase()} · {formatBytes(item.size_bytes)}{item.width && item.height ? ` · ${item.width} × ${item.height}` : ''}</p>
      <p className="review-location" title="Folder name only; full local paths stay private">Folder: {item.location}</p>
      {item.reason && <p className="review-reason">{item.reason}</p>}
      {item.review_status !== 'pending' && <span className="review-status-tag">{item.review_status === 'later' ? 'Keep for later' : 'Reviewed'}</span>}
      <div className="review-actions">
        <button className="quiet-button" onClick={() => void onMark(item.id, 'reviewed')} disabled={busy || item.review_status === 'reviewed'}>Reviewed</button>
        <button className="text-button" onClick={() => void onMark(item.id, item.review_status === 'later' ? 'pending' : 'later')} disabled={busy}>{item.review_status === 'later' ? 'Return to review' : 'Keep for later'}</button>
      </div>
    </div>
  </article>
})

function pageItemsFor(result: PhotoReviewResult, category: PhotoReviewCategory): ReviewMediaItem[] {
  return category === 'exact' || category === 'similar' ? result.groups.flatMap((group) => group.items) : result.items
}

function message(error: unknown, fallback: string): string { return error instanceof Error ? error.message : fallback }
function formatBytes(bytes: number): string { return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1_000))} KB` }
