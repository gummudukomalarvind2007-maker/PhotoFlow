import { Icon } from './Icon'

interface ScanProgressProps { scan: ScanStatus; onCancel: () => void }

export function ScanProgress({ scan, onCancel }: ScanProgressProps) {
  const working = scan.status === 'running'
  const discovering = working && scan.processed_files === 0
  const percent = scan.discovered_files
    ? Math.min(100, Math.round((scan.processed_files / scan.discovered_files) * 100))
    : scan.status === 'completed' ? 100 : 0
  const status = scan.status.charAt(0).toUpperCase() + scan.status.slice(1)
  return <section className="scan-progress" aria-live="polite">
    <span className="scan-icon"><Icon name={working ? 'rotate' : scan.status === 'completed' ? 'check' : 'bell'} size={17} /></span>
    <div className="scan-copy">
      <strong>{discovering ? 'Running · discovering supported files' : `${status} scan`}</strong>
      <p>{scan.discovered_files} discovered · {scan.processed_files} processed · {scan.indexed_files} indexed · {scan.skipped_files} skipped · {scan.failed_files} failed</p>
      <div className={`progress${discovering ? ' discovering' : ''}`}><span style={{ width: discovering ? '28%' : `${percent}%` }} /></div>
      {scan.error_summary && <p className="scan-error-detail">{scan.error_summary}</p>}
    </div>
    {working && <button className="scan-cancel" onClick={onCancel}>Cancel scan</button>}
  </section>
}
