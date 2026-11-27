import { useCallback, useEffect, useState } from 'react'
import { ActivityFeed } from './components/ActivityFeed'
import { Albums } from './components/Albums'
import { ExportWorkspace } from './components/ExportWorkspace'
import { Icon } from './components/Icon'
import { MediaLibrary } from './components/MediaLibrary'
import { Occasions } from './components/Occasions'
import { QualityReview } from './components/QualityReview'
import { RetryFailed } from './components/RetryFailed'
import { ScanRecovery } from './components/ScanRecovery'
import { ScanProgress } from './components/ScanProgress'
import { Settings } from './components/Settings'
import { SmartCollections } from './components/SmartCollections'
import { Sidebar } from './components/Sidebar'
import { StatCard } from './components/StatCard'
import { StatusPanel } from './components/StatusPanel'
import { TransferProgress } from './components/TransferProgress'
import { thumbnailCache } from './components/thumbnailCache'
import type { NavigationItem } from './types'

export function App() {
  const [active, setActive] = useState<NavigationItem>('Dashboard')
  const [version, setVersion] = useState('…')
  const [overview, setOverview] = useState<LibraryOverview>({ photos: 0, videos: 0, albums: 0, total_bytes: 0 })
  const [scanHistory, setScanHistory] = useState<ScanStatus[]>([])
  const [retryCount, setRetryCount] = useState(0)
  const [scan, setScan] = useState<ScanStatus>()
  const [recoverableScan, setRecoverableScan] = useState<ScanStatus>()
  const [recoveryError, setRecoveryError] = useState<string>()
  const [operationError, setOperationError] = useState<string>()
  const [search, setSearch] = useState('')
  const [transfer, setTransfer] = useState<TransferJob>()
  const transferBusy = transfer?.status === 'running'
  const visibleScan = scan ?? scanHistory[0]

  const startTransfer = useCallback((job: TransferJob) => setTransfer(job), [])
  const updateTransfer = useCallback((job: TransferJob) => setTransfer(job), [])
  const dismissTransfer = useCallback(() => setTransfer(undefined), [])

  const refreshOverview = useCallback(async () => {
    try {
      setOverview(await window.photoFlow.getLibraryOverview())
      setOperationError(undefined)
    } catch (error: unknown) {
      setOperationError(errorMessage(error, 'Could not refresh library statistics.'))
    }
  }, [])
  const refreshScanHistory = useCallback(async () => {
    try {
      setScanHistory(await window.photoFlow.getScanHistory())
    } catch (error: unknown) {
      setOperationError(errorMessage(error, 'Could not load scan history.'))
    }
  }, [])
  const refreshRetryCount = useCallback(async () => {
    try {
      const failures = await window.photoFlow.getFailedItems()
      setRetryCount(failures.scan_failures.length + failures.analysis_failures.length)
    } catch (error: unknown) {
      setOperationError(errorMessage(error, 'Could not refresh failed-item count.'))
    }
  }, [])
  const refreshRecoverableScan = useCallback(() => {
    void window.photoFlow.getResumableScan().then((result) => {
      setRecoverableScan(result ?? undefined)
      setRecoveryError(undefined)
    }).catch((error: unknown) => {
      setRecoveryError(errorMessage(error, 'Could not check for a recoverable scan.'))
    })
  }, [])
  const refreshLibraryState = useCallback(() => {
    void refreshOverview()
    void refreshScanHistory()
    void refreshRetryCount()
  }, [refreshOverview, refreshRetryCount, refreshScanHistory])

  useEffect(() => {
    void window.photoFlow.getVersion().then(setVersion).catch((error: unknown) => setOperationError(errorMessage(error, 'Could not read the application version.')))
    void window.photoFlow.getBackendHealth().catch((error: unknown) => {
      setOperationError(errorMessage(error, 'Could not check backend health.'))
    })
    refreshLibraryState()
    refreshRecoverableScan()
  }, [refreshLibraryState, refreshRecoverableScan])
  useEffect(() => {
    if (!scan || scan.status !== 'running') return
    const timer = window.setInterval(() => {
      void window.photoFlow.getScanStatus(scan.id).then((updated) => {
        setScan(updated)
        if (updated.status !== 'running') {
          refreshLibraryState()
          refreshRecoverableScan()
        }
      }).catch((error: unknown) => setOperationError(errorMessage(error, 'Could not refresh scan progress.')))
    }, 1_500)
    return () => window.clearInterval(timer)
  }, [scan?.id, scan?.status, refreshLibraryState, refreshRecoverableScan])
  useEffect(() => {
    thumbnailCache.clear()
  }, [scan?.status])

  const addMedia = useCallback(() => {
    if (scan?.status === 'running') return
    setOperationError(undefined)
    void window.photoFlow.selectAndScan().then((result) => {
      if (result.scan) {
        setScan(result.scan)
        setRecoverableScan(undefined)
      }
    }).catch((error: unknown) => setOperationError(errorMessage(error, 'Could not start the media scan.')))
  }, [scan?.status])
  const cancelScan = useCallback(() => {
    if (!scan) return
    setOperationError(undefined)
    void window.photoFlow.cancelScan(scan.id).then(setScan).catch((error: unknown) => setOperationError(errorMessage(error, 'Could not cancel the scan.')))
  }, [scan])
  const resumeScan = useCallback(() => {
    if (!recoverableScan) return
    setRecoveryError(undefined)
    setOperationError(undefined)
    void window.photoFlow.resumeScan(recoverableScan.id).then((resumed) => {
      setScan(resumed)
      setRecoverableScan(undefined)
    }).catch((error: unknown) => setRecoveryError(errorMessage(error, 'Could not resume this scan.')))
  }, [recoverableScan])
  const scanRetryStarted = useCallback((retried: ScanStatus) => {
    setScan(retried)
    setOperationError(undefined)
    void refreshScanHistory()
  }, [refreshScanHistory])

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <Sidebar active={active} onSelect={setActive} retryCount={retryCount} />
    <main className="workspace" id="main-content" tabIndex={-1}>
      <header className="topbar">
        <div className="crumb">LIBRARY <span>/</span> {active.toUpperCase()}</div>
        <div className="top-actions">
          <label className="search"><Icon name="search" size={18} /><input type="search" list="media-search-suggestions" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search filenames, format, camera" aria-label="Search filenames, format, or camera" /><datalist id="media-search-suggestions">{['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.heic', '.heif', '.mp4', '.mov', '.avi', '.mkv', '.webm'].map((extension) => <option key={extension} value={extension} />)}</datalist></label>
        </div>
      </header>
      {recoverableScan && <ScanRecovery scan={recoverableScan} onResume={resumeScan} disabled={scan?.status === 'running'} error={recoveryError} />}
      {!recoverableScan && recoveryError && <p className="app-error" role="alert">{recoveryError}</p>}
      {operationError && <p className="app-error" role="alert">{operationError}</p>}
      {transfer && <TransferProgress job={transfer} onUpdate={updateTransfer} onDismiss={dismissTransfer} />}
      {active === 'Dashboard'
        ? <Dashboard version={version} overview={overview} scan={visibleScan} scans={scanHistory} onAddMedia={addMedia} onCancel={cancelScan} onOpenReview={() => setActive('Quality Review')} />
        : active === 'Photos' || active === 'Videos'
          ? <MediaLibrary initialType={active === 'Photos' ? 'photo' : 'video'} search={search} onSearchChange={setSearch} scan={scan} onAddMedia={addMedia} onCancel={cancelScan} scanRunning={scan?.status === 'running'} onOpenAlbums={() => setActive('Albums')} onTransferStarted={startTransfer} transferBusy={transferBusy} onMediaRemoved={refreshLibraryState} />
          : active === 'Smart Collections'
            ? <SmartCollections scanStatus={scan?.status} />
            : active === 'Albums'
            ? <Albums onTransferStarted={startTransfer} transferBusy={transferBusy} />
            : active === 'Occasions'
              ? <Occasions />
              : active === 'Export'
                ? <ExportWorkspace onTransferStarted={startTransfer} transferBusy={transferBusy} />
                : active === 'Settings'
                  ? <Settings onTransferStarted={startTransfer} transferBusy={transferBusy} />
                  : active === 'Retry Failed'
                    ? <RetryFailed scan={scan} onScanStarted={scanRetryStarted} onFailureCountChanged={setRetryCount} />
                    : <QualityReview key={active} initialCategory={active === 'Duplicates' ? 'exact' : 'blurry'} />}
    </main>
  </div>
}

interface DashboardProps {
  version: string
  overview: LibraryOverview
  scan?: ScanStatus
  scans: ScanStatus[]
  onAddMedia: () => void
  onCancel: () => void
  onOpenReview: () => void
}

function Dashboard({ version, overview, scan, scans, onAddMedia, onCancel, onOpenReview }: DashboardProps) {
  return <div className="dashboard">
    <section className="welcome"><div><p className="section-kicker">OVERVIEW</p><h1>Your library, at a glance.</h1><p className="subcopy">A calm, private space for the memories you keep.</p></div><button className="primary-button" onClick={onAddMedia} disabled={scan?.status === 'running'}><Icon name="plus" size={18} /> Add media</button></section>
    {scan && <ScanProgress scan={scan} onCancel={onCancel} />}
    <section className="stats-grid" aria-label="Library statistics">
      <StatCard icon="image" tone="purple" label="Photos" value={overview.photos.toLocaleString()} trend="Indexed photos" />
      <StatCard icon="video" tone="blue" label="Videos" value={overview.videos.toLocaleString()} trend="Indexed videos" />
      <StatCard icon="folder" tone="orange" label="Library size" value={formatLibrarySize(overview.total_bytes)} trend="Total size of indexed originals" />
    </section>
    <section className="storage-card">
      <div className="storage-icon"><Icon name="database" /></div>
      <div className="storage-main"><p className="section-kicker">PHOTOFLOW DATABASE</p><div className="storage-title"><h2>{formatLibrarySize(overview.total_bytes)} <span>indexed media</span></h2><span className="text-button">Local-only library</span></div><p>Original media remains in its source folder <span>•</span> thumbnails stay in PhotoFlow cache</p></div>
      <div className="storage-breakdown"><span><i className="photo-key" />Photos <b>{overview.photos}</b></span><span><i className="video-key" />Videos <b>{overview.videos}</b></span></div>
    </section>
    <section className="content-grid"><ActivityFeed scans={scans} /><StatusPanel scan={scan} /></section>
    <section className="insight-banner"><div className="insight-glyph"><Icon name="sparkle" size={23} /></div><div><p className="section-kicker">PHOTO REVIEW</p><h2>Review duplicates and quality signals</h2><p>Exact matches and visually similar photos are grouped separately. Analysis never changes your originals.</p></div><button className="quiet-button" onClick={onOpenReview}>Open Quality Review <Icon name="arrow-up" size={16} /></button></section>
    <footer className="app-footer"><span>PhotoFlow v{version}</span></footer>
  </div>
}

function formatLibrarySize(bytes: number) {
  if (!bytes) return '0 B'
  if (bytes < 1_000_000) return `${Math.round(bytes / 1_000)} KB`
  if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`
  return `${(bytes / 1_000_000_000).toFixed(2)} GB`
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
