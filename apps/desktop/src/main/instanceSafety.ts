import type { BrowserWindow } from 'electron'

export function focusApplicationWindow(window: BrowserWindow | undefined): void {
  if (!window || window.isDestroyed()) return
  if (window.isMinimized()) window.restore()
  window.show()
  window.focus()
}

interface SingleInstanceApp {
  requestSingleInstanceLock(): boolean
  quit(): void
  on(event: 'second-instance', listener: () => void): unknown
}

export function initializeSingleInstance(
  application: SingleInstanceApp,
  focusExistingWindow: () => void
): boolean {
  const hasLock = application.requestSingleInstanceLock()
  if (!hasLock) {
    application.quit()
    return false
  }
  application.on('second-instance', focusExistingWindow)
  return true
}
