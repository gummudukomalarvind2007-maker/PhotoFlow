import { useState } from 'react'
import { Icon } from './Icon'

interface ExportWorkspaceProps {
  onTransferStarted: (job: TransferJob) => void
  transferBusy: boolean
}

export function ExportWorkspace({ onTransferStarted, transferBusy }: ExportWorkspaceProps) {
  const [mediaType, setMediaType] = useState<'all' | 'photo' | 'video'>('all')
  const [organize, setOrganize] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [message, setMessage] = useState<string>()

  const perform = async (operation: () => Promise<{ cancelled: boolean; job?: TransferJob }>, success: string): Promise<void> => {
    setBusy(true)
    setError(undefined)
    setMessage(undefined)
    try {
      const result = await operation()
      if (!result.cancelled && result.job) {
        onTransferStarted(result.job)
        setMessage(success)
      }
    } catch (operationError) {
      setError(errorMessage(operationError, 'Could not start this operation.'))
    } finally {
      setBusy(false)
    }
  }

  return <section className="export-page">
    <header className="operation-page-header"><div><p className="section-kicker">TAKE YOUR LIBRARY WITH YOU</p><h1>Export &amp; backup</h1><p>Copy original media or protect your PhotoFlow library. Files are never recompressed or overwritten.</p></div></header>
    {error && <p className="app-error operation-error" role="alert">{error}</p>}
    {message && <p className="album-success" role="status">{message}</p>}

    <section className="operation-section">
      <div className="operation-section-title"><div><p className="section-kicker">MEDIA EXPORT</p><h2>Copy original files</h2><p>Choose media from Photos, Videos, or an album—or export this whole library selection.</p></div><div className="operation-icon"><Icon name="arrow-up" /></div></div>
      <div className="export-options">
        <label className="filter-field"><span>Media type</span><select value={mediaType} onChange={(event) => setMediaType(event.target.value as 'all' | 'photo' | 'video')}><option value="all">Photos &amp; videos</option><option value="photo">Photos</option><option value="video">Videos</option></select></label>
        <label className="operation-check"><input type="checkbox" checked={organize} onChange={(event) => setOrganize(event.target.checked)} /><span>Organize into Photos and Videos folders</span></label>
        <button className="primary-button" disabled={busy || transferBusy} onClick={() => void perform(
          () => window.photoFlow.exportFilteredMedia(mediaType, {}, organize),
          'Media export started.'
        )}><Icon name="folder" size={16} /> Export {mediaType === 'all' ? 'all media' : `${mediaType}s`}</button>
      </div>
      <p className="operation-hint">To export a filtered search, use Export matching from Photos or Videos. Selected items and album exports are available in their respective views.</p>
    </section>

    <section className="operation-section">
      <div className="operation-section-title"><div><p className="section-kicker">METADATA EXPORT</p><h2>Export library information</h2><p>CSV or JSON with filenames, formats, dimensions, capture dates, cameras, and video details. GPS and internal paths are excluded.</p></div><div className="operation-icon blue"><Icon name="database" /></div></div>
      <div className="operation-actions">
        <button className="quiet-button" disabled={busy || transferBusy} onClick={() => void perform(() => window.photoFlow.exportMetadata('csv'), 'CSV metadata export started.')}>Export as CSV</button>
        <button className="quiet-button" disabled={busy || transferBusy} onClick={() => void perform(() => window.photoFlow.exportMetadata('json'), 'JSON metadata export started.')}>Export as JSON</button>
      </div>
    </section>

    <section className="operation-section">
      <div className="operation-section-title"><div><p className="section-kicker">DATABASE BACKUP</p><h2>Back up or restore your library</h2><p>Backups use SQLite’s online backup API to include a consistent database snapshot, including WAL data. Restore makes a safety backup first.</p></div><div className="operation-icon green"><Icon name="database" /></div></div>
      <div className="operation-actions">
        <button className="quiet-button" disabled={busy || transferBusy} onClick={() => void perform(() => window.photoFlow.createDatabaseBackup(), 'Database backup started.')}>Create backup</button>
        <button className="quiet-button danger-button" disabled={busy || transferBusy} onClick={() => void perform(() => window.photoFlow.restoreDatabaseBackup(), 'Restore started.')}>Restore from backup…</button>
      </div>
      <p className="operation-hint">Restoring replaces the PhotoFlow database only. Original photos and videos remain in their folders.</p>
    </section>
  </section>
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
