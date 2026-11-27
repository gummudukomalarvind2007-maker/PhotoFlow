import { app } from 'electron'
import { appendFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

type LogLevel = 'INFO' | 'WARN' | 'ERROR'

function write(level: LogLevel, message: string, error?: unknown): void {
  const line = `${new Date().toISOString()} [${level}] ${message}${error ? ` ${String(error)}` : ''}\n`
  console[level === 'ERROR' ? 'error' : level === 'WARN' ? 'warn' : 'log'](line.trim())

  try {
    const directory = join(app.getPath('userData'), 'logs')
    mkdirSync(directory, { recursive: true })
    appendFileSync(join(directory, 'photoflow.log'), line, 'utf8')
  } catch {
    // Logging must never prevent startup or shutdown.
  }
}

export const logger = {
  info: (message: string): void => write('INFO', message),
  warn: (message: string, error?: unknown): void => write('WARN', message, error),
  error: (message: string, error?: unknown): void => write('ERROR', message, error)
}

