import { describe, expect, it } from 'vitest'
import { recoverTransferPoll } from './transferRecovery'

const runningJob: TransferJob = {
  id: 'transfer-1',
  operation: 'backup',
  status: 'running',
  total: 10,
  completed: 4,
  succeeded: 0,
  failed: 0,
  cancel_requested: false
}

describe('transfer progress recovery', () => {
  it('stops polling an operation that the restarted backend no longer knows', () => {
    const recovery = recoverTransferPoll(runningJob, new Error('Unknown transfer operation'))

    expect(recovery.retry).toBe(false)
    expect(recovery.job.status).toBe('interrupted')
    expect(recovery.job.error_summary).toContain('Check destination files')
    expect(recovery.job.completed).toBe(4)
  })

  it('keeps retrying transient progress errors', () => {
    const recovery = recoverTransferPoll(runningJob, new Error('Backend communication failed'))

    expect(recovery.retry).toBe(true)
    expect(recovery.job).toBe(runningJob)
    expect(recovery.message).toBe('Could not refresh operation progress.')
  })
})
