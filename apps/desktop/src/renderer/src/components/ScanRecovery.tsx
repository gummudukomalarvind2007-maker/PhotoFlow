import { Icon } from './Icon'

interface ScanRecoveryProps {
  scan: ScanStatus
  onResume: () => void
  disabled: boolean
  error?: string
}

export function ScanRecovery({ scan, onResume, disabled, error }: ScanRecoveryProps) {
  const status = scan.status.charAt(0).toUpperCase() + scan.status.slice(1)
  return <section className="scan-progress scan-recovery" aria-live="polite">
    <span className="scan-icon"><Icon name="rotate" size={17} /></span>
    <div className="scan-copy">
      <strong>{status} scan available to resume</strong>
      <p>{scan.discovered_files} discovered · {scan.processed_files} processed · {scan.indexed_files} indexed · {scan.skipped_files} skipped · {scan.failed_files} failed</p>
      {scan.error_summary && <p className="scan-error-detail">{scan.error_summary}</p>}
      {error && <p className="scan-error-detail" role="alert">{error}</p>}
    </div>
    <button className="primary-button scan-resume" onClick={onResume} disabled={disabled}>Resume scan</button>
  </section>
}
