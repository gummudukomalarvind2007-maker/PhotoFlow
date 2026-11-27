export interface BackendHealth {
  status: 'ok' | 'unavailable'
  pythonVersion?: string
  sqliteVersion?: string
  databasePath?: string
  message?: string
}

