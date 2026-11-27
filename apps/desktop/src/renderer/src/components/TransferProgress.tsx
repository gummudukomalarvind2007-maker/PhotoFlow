import { useEffect, useRef, useState } from 'react'
import { recoverTransferPoll } from './transferRecovery'

interface TransferProgressProps {
  job: TransferJob
  onUpdate: (job: TransferJob) => void
  onDismiss: () => void
}

export function TransferProgress({ job, onUpdate, onDismiss }: TransferProgressProps) {
  const [current, setCurrent] = useState(job)
  const [pollError, setPollError] = useState<string>()
  const currentRef = useRef(current)

  useEffect(() => {
    currentRef.current = current
  }, [current])

  useEffect(() => {
    let active = true
    let timer: number | undefined
    const poll = async (): Promise<void> => {
      try {
        const updated = await window.photoFlow.getTransferStatus(job.id)
        if (!active) return
        currentRef.current = updated
        setCurrent(updated)
        onUpdate(updated)
        setPollError(undefined)
        if (updated.status === 'running') timer = window.setTimeout(() => void poll(), 900)
      } catch (error: unknown) {
        if (!active) return
        const recovery = recoverTransferPoll(currentRef.current, error)
        currentRef.current = recovery.job
        setCurrent(recovery.job)
        onUpdate(recovery.job)
        setPollError(recovery.message)
        if (recovery.retry) timer = window.setTimeout(() => void poll(), 1500)
      }
    }
    if (job.status === 'running') timer = window.setTimeout(() => void poll(), 500)
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [job.id])

  const cancel = (): void => {
    void window.photoFlow.cancelTransfer(job.id).then((updated) => {
      setCurrent(updated)
      onUpdate(updated)
    }).catch(() => setPollError('Could not cancel this operation.'))
  }
  const percent = current.total > 0
    ? Math.min(100, Math.round((current.completed / current.total) * 100))
    : current.status === 'completed' ? 100 : 0
  const running = current.status === 'running'

  return <section className={`transfer-progress ${running ? 'running' : current.status}`} aria-live="polite">
    <div className="transfer-heading">
      <div><p className="section-kicker">LOCAL OPERATION</p><strong>{operationTitle(current.operation)}</strong>
        <span>{running ? `${percent}% · ${current.completed.toLocaleString()} of ${current.total.toLocaleString()} ${operationUnit(current.operation)}` : statusLabel(current.status)}</span>
      </div>
      {running
        ? <button className="scan-cancel" onClick={cancel} disabled={current.cancel_requested}>Cancel</button>
        : <button className="text-button" onClick={onDismiss}>Dismiss</button>}
    </div>
    <div className="transfer-track"><span style={{ width: `${percent}%` }} /></div>
    <p className="transfer-counts">
      {current.succeeded.toLocaleString()} succeeded <span>·</span> {current.failed.toLocaleString()} failed
      {current.error_summary && <span className="transfer-message"> · {current.error_summary}</span>}
      {pollError && <span className="transfer-message"> · {pollError}</span>}
    </p>
  </section>
}

function operationTitle(operation: TransferJob['operation']): string {
  return {
    'media-export': 'Exporting original media',
    'metadata-export': 'Exporting library metadata',
    backup: 'Backing up PhotoFlow database',
    restore: 'Restoring PhotoFlow database',
    'cache-clear': 'Clearing thumbnail cache'
  }[operation]
}

function operationUnit(operation: TransferJob['operation']): string {
  return operation === 'backup' ? 'database pages' : operation === 'restore' ? 'restore steps' : 'items'
}

function statusLabel(status: TransferJob['status']): string {
  if (status === 'completed') return 'Completed'
  if (status === 'cancelled') return 'Cancelled'
  if (status === 'interrupted') return 'Status unavailable'
  return 'Failed'
}
