import { Icon } from './Icon'

export function StatusPanel({ scan }: { scan?: ScanStatus }) {
  const percent = scan?.discovered_files
    ? Math.min(100, Math.round((scan.processed_files / scan.discovered_files) * 100))
    : scan?.status === 'completed' ? 100 : 0
  const running = scan?.status === 'running'
  const hasProgress = Boolean(scan?.discovered_files)
  return <section className="panel status-panel">
    <div className="panel-heading">
      <div><p className="section-kicker">SCAN STATUS</p><h2>Processing status</h2></div>
      <span className="process-icon"><Icon name={running ? 'rotate' : 'sparkle'} /></span>
    </div>
    <div className="process-card">
      <div className="process-top">
        <span className="process-icon"><Icon name={running ? 'rotate' : scan?.status === 'completed' ? 'check' : 'bell'} /></span>
        <div>
          <strong>{scan ? `${scan.status.charAt(0).toUpperCase()}${scan.status.slice(1)} scan` : 'No scan activity yet'}</strong>
          <p>{scan ? hasProgress ? `${scan.processed_files} processed of ${scan.discovered_files} discovered` : running ? 'Discovering supported files' : 'No supported files discovered' : 'Add folders to index local media'}</p>
        </div>
        {scan && hasProgress && <span className="percentage">{percent}%</span>}
      </div>
      {hasProgress && <div className="progress"><span style={{ width: `${percent}%` }} /></div>}
      <p className="process-note">
        {scan
          ? `${scan.indexed_files} indexed · ${scan.skipped_files} skipped · ${scan.failed_files} failed`
          : 'Progress and results will appear here'}
      </p>
      {scan?.error_summary && <p className="scan-error-detail">{scan.error_summary}</p>}
    </div>
  </section>
}
