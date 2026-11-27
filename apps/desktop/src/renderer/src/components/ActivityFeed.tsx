import { Icon } from './Icon'

export function ActivityFeed({ scans }: { scans: ScanStatus[] }) {
  return <section className="panel activity-panel">
    <div className="panel-heading"><div><p className="section-kicker">LIBRARY HISTORY</p><h2>Recent activity</h2></div></div>
    {scans.length
      ? <div className="activity-list">{scans.map((scan) => {
        const failed = scan.failed_files > 0
        const title = scan.status === 'running'
          ? 'Scan in progress'
          : scan.status === 'completed' && failed
            ? 'Scan completed with failures'
            : `${capitalize(scan.status)} scan`
        return <article className="activity-item" key={scan.id}>
          <span className={`activity-icon ${scan.status === 'failed' || failed ? 'notice' : scan.status === 'completed' ? 'complete' : 'import'}`}>
            <Icon name={scan.status === 'completed' && !failed ? 'check' : scan.status === 'running' ? 'rotate' : 'folder'} size={17} />
          </span>
          <div><strong>{title}</strong><p>{scan.indexed_files} indexed · {scan.skipped_files} skipped · {scan.failed_files} failed</p></div>
          <time>{formatTime(scan.completed_at ?? scan.started_at)}</time>
        </article>
      })}</div>
      : <div className="activity-empty"><Icon name="clock" size={18} /><p>No scan activity yet. Scans will appear here after you add a folder.</p></div>}
  </section>
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(`${value.replace(' ', 'T')}Z`)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
