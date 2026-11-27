import { useCallback, useEffect, useState } from 'react'
import { Icon } from './Icon'

interface RetryFailedProps {
  scan?: ScanStatus
  onScanStarted: (scan: ScanStatus) => void
  onFailureCountChanged: (count: number) => void
}

export function RetryFailed({ scan, onScanStarted, onFailureCountChanged }: RetryFailedProps) {
  const [failures, setFailures] = useState<FailedItems>({ scan_failures: [], analysis_failures: [] })
  const [analysisJob, setAnalysisJob] = useState<PhotoReviewJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [message, setMessage] = useState<string>()

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(undefined)
    try {
      const current = await window.photoFlow.getFailedItems()
      setFailures(current)
      onFailureCountChanged(current.scan_failures.length + current.analysis_failures.length)
    } catch (loadError) {
      setError(errorMessage(loadError, 'Could not load failed items.'))
    } finally {
      setLoading(false)
    }
  }, [onFailureCountChanged])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!scan || scan.status === 'running') return
    void refresh()
  }, [scan?.id, scan?.status, refresh])
  useEffect(() => {
    if (!analysisJob || analysisJob.status !== 'running') return
    const timer = window.setInterval(() => {
      void window.photoFlow.getPhotoReviewStatus(analysisJob.id).then((updated) => {
        if (!updated) return
        setAnalysisJob(updated)
        if (updated.status !== 'running') void refresh()
      }).catch((pollError: unknown) => setError(errorMessage(pollError, 'Could not refresh photo-analysis progress.')))
    }, 1_500)
    return () => window.clearInterval(timer)
  }, [analysisJob?.id, analysisJob?.status, refresh])

  const retryScan = async (scanId: string): Promise<void> => {
    setBusy(true)
    setError(undefined)
    setMessage(undefined)
    try {
      onScanStarted(await window.photoFlow.retryFailedScan(scanId))
      setMessage('Retry started. Unchanged successful files will be skipped.')
    } catch (retryError) {
      setError(errorMessage(retryError, 'Could not retry this scan.'))
    } finally {
      setBusy(false)
    }
  }

  const retryAnalysis = async (): Promise<void> => {
    setError(undefined)
    setMessage(undefined)
    try {
      const job = await window.photoFlow.retryFailedPhotoAnalysis()
      setAnalysisJob(job)
      setMessage('Retrying failed photo-analysis items.')
    } catch (retryError) {
      setError(errorMessage(retryError, 'Could not retry photo analysis.'))
    }
  }

  const count = failures.scan_failures.length + failures.analysis_failures.length
  return <section className="retry-page">
    <header className="operation-page-header">
      <div><p className="section-kicker">RECOVERY</p><h1>Retry failed</h1><p>Review actual scan and photo-analysis failures. Successful indexed files are not processed again.</p></div>
      <button className="quiet-button" onClick={() => void refresh()} disabled={loading}><Icon name="rotate" size={15} /> Refresh</button>
    </header>
    {error && <p className="app-error operation-error" role="alert">{error}</p>}
    {message && <p className="album-success" role="status">{message}</p>}
    {analysisJob && <div className="review-job" aria-live="polite">
      <div className="review-job-heading"><strong>Photo analysis {analysisJob.status}</strong><span>{analysisJob.processed_files} / {analysisJob.total_files} checked · {analysisJob.failed_files} failed</span></div>
      {analysisJob.error_summary && <p className="review-error-detail">{analysisJob.error_summary}</p>}
    </div>}
    {loading
      ? <div className="collection-empty" role="status">Loading failed items…</div>
      : count === 0
        ? <div className="collection-empty retry-empty"><Icon name="check" size={30} /><h2>No failed items</h2><p>Failed scans and photo-analysis items will appear here if a problem occurs.</p></div>
        : <div className="retry-sections">
          {failures.scan_failures.length > 0 && <section className="settings-section">
            <div className="settings-section-heading"><p className="section-kicker">SCAN FAILURES · {failures.scan_failures.length}</p></div>
            <div className="retry-list">{failures.scan_failures.map((item) => <article className="retry-item" key={`${item.kind}:${item.id}`}>
              <span className="retry-item-icon"><Icon name="folder" size={17} /></span>
              <div><strong>{item.filename}</strong><p>{item.error_summary}</p></div>
              <button className="quiet-button" onClick={() => item.scan_id && void retryScan(item.scan_id)} disabled={busy || scan?.status === 'running' || !item.scan_id}>Retry scan</button>
            </article>)}</div>
          </section>}
          {failures.analysis_failures.length > 0 && <section className="settings-section">
            <div className="settings-section-heading"><p className="section-kicker">PHOTO-ANALYSIS FAILURES · {failures.analysis_failures.length}</p>
              <button className="quiet-button" onClick={() => void retryAnalysis()} disabled={analysisJob?.status === 'running'}>Retry failed analysis</button>
            </div>
            <div className="retry-list">{failures.analysis_failures.map((item) => <article className="retry-item" key={`analysis:${item.id}`}>
              <span className="retry-item-icon"><Icon name="image" size={17} /></span>
              <div><strong>{item.filename}</strong><p>{item.error_summary}</p></div>
              <span className="retry-item-status">Failed</span>
            </article>)}</div>
          </section>}
        </div>}
  </section>
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
