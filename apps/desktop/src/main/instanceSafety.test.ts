import { describe, expect, it, vi } from 'vitest'
import { focusApplicationWindow, initializeSingleInstance } from './instanceSafety'

describe('single-instance behavior', () => {
  it('quits a duplicate process when the lock is unavailable', () => {
    const quit = vi.fn()
    const on = vi.fn()
    const application = {
      requestSingleInstanceLock: () => false,
      quit,
      on
    }

    expect(initializeSingleInstance(application, vi.fn())).toBe(false)
    expect(quit).toHaveBeenCalledOnce()
    expect(on).not.toHaveBeenCalled()
  })

  it('registers second-instance focus when the lock is acquired', () => {
    const quit = vi.fn()
    const on = vi.fn()
    const focusExisting = vi.fn()
    const application = {
      requestSingleInstanceLock: () => true,
      quit,
      on
    }

    expect(initializeSingleInstance(application, focusExisting)).toBe(true)
    expect(quit).not.toHaveBeenCalled()
    expect(on).toHaveBeenCalledWith('second-instance', focusExisting)
  })

  it('restores, shows, and focuses the existing window', () => {
    const window = {
      isDestroyed: () => false,
      isMinimized: () => true,
      restore: vi.fn(),
      show: vi.fn(),
      focus: vi.fn()
    }

    focusApplicationWindow(window as never)

    expect(window.restore).toHaveBeenCalledOnce()
    expect(window.show).toHaveBeenCalledOnce()
    expect(window.focus).toHaveBeenCalledOnce()
  })
})
