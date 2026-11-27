interface TransferPollRecovery {
  job: TransferJob
  retry: boolean
  message?: string
}

export function recoverTransferPoll(job: TransferJob, error: unknown): TransferPollRecovery {
  if (error instanceof Error && error.message === 'Unknown transfer operation') {
    const message = 'Operation status is unavailable. Check destination files before retrying.'
    return {
      job: {
        ...job,
        status: 'interrupted',
        cancel_requested: false,
        error_summary: message
      },
      retry: false
    }
  }

  return {
    job,
    retry: true,
    message: 'Could not refresh operation progress.'
  }
}
