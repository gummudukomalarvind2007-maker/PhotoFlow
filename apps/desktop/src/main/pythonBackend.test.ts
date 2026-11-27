import { EventEmitter } from 'node:events'
import { PassThrough, Writable } from 'node:stream'
import type { ChildProcessWithoutNullStreams } from 'node:child_process'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { spawn } from 'node:child_process'
import { PythonBackend } from './pythonBackend'

vi.mock('node:child_process', () => ({ spawn: vi.fn() }))
vi.mock('node:fs', () => ({ existsSync: () => true }))
vi.mock('electron', () => ({ app: { getAppPath: () => 'C:\\PhotoFlow\\apps\\desktop' } }))
vi.mock('./logger', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() }
}))

class FakeChild extends EventEmitter {
  readonly requests: string[] = []
  readonly stdin: Writable
  readonly stdout = new PassThrough()
  readonly stderr = new PassThrough()
  killed = false
  exitCode: number | null = null
  signalCode: NodeJS.Signals | null = null
  writeError?: Error
  onRequest?: (request: Record<string, unknown>) => void

  constructor(start = false) {
    super()
    this.stdin = new Writable({
      write: (chunk, _encoding, callback) => {
        if (this.writeError) {
          const error = this.writeError
          this.writeError = undefined
          callback(error)
          return
        }
        const request = String(chunk).trim()
        this.requests.push(request)
        this.onRequest?.(JSON.parse(request) as Record<string, unknown>)
        callback()
      },
      final: (callback) => {
        this.exitCode = 0
        this.emit('exit', 0, null)
        callback()
      }
    })
    if (start) this.start()
  }

  start(): void {
    queueMicrotask(() => this.emit('spawn'))
  }

  kill = vi.fn(() => {
    this.killed = true
    this.signalCode = 'SIGTERM'
    this.emit('exit', null, 'SIGTERM')
    return true
  })

  respond(request: Record<string, unknown>): void {
    this.stdout.write(`${JSON.stringify({
      id: request.id,
      ok: true,
      overview: { photos: 0, videos: 0, albums: 0, total_bytes: 0 }
    })}\n`)
  }
}

const spawnMock = vi.mocked(spawn)

function installChild(child: FakeChild): void {
  spawnMock.mockImplementation(() => {
    child.start()
    return child as unknown as ChildProcessWithoutNullStreams
  })
}

async function waitForRequest(child: FakeChild): Promise<void> {
  for (let attempt = 0; attempt < 10 && child.requests.length === 0; attempt += 1) {
    await new Promise<void>((resolve) => setImmediate(resolve))
  }
}

describe('PythonBackend process reliability', () => {
  beforeEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('shares one startup between simultaneous requests', async () => {
    const child = new FakeChild()
    child.onRequest = (request) => child.respond(request)
    installChild(child)
    const backend = new PythonBackend()

    const results = await Promise.all([backend.libraryOverview(), backend.libraryOverview()])

    expect(spawnMock).toHaveBeenCalledTimes(1)
    expect(results).toHaveLength(2)
    await backend.stop()
  })

  it('rejects pending requests immediately after an unexpected process exit', async () => {
    const child = new FakeChild()
    installChild(child)
    const backend = new PythonBackend()
    const request = backend.libraryOverview()
    await waitForRequest(child)

    child.emit('exit', 2, null)

    await expect(request).rejects.toThrow('exited unexpectedly')
  })

  it('kills a child that times out during startup and permits a retry', async () => {
    vi.useFakeTimers()
    const timedOutChild = new FakeChild(false)
    const retryChild = new FakeChild()
    retryChild.onRequest = (request) => retryChild.respond(request)
    spawnMock.mockImplementationOnce(() => timedOutChild as unknown as ChildProcessWithoutNullStreams)
    spawnMock.mockImplementationOnce(() => {
      retryChild.start()
      return retryChild as unknown as ChildProcessWithoutNullStreams
    })
    const backend = new PythonBackend()

    const failedRequest = backend.libraryOverview()
    const failure = expect(failedRequest).rejects.toThrow('Timed out starting Python backend')
    vi.advanceTimersByTime(5_000)
    await failure
    expect(timedOutChild.kill).toHaveBeenCalled()

    vi.useRealTimers()
    const retriedOverview = await backend.libraryOverview()
    expect(retriedOverview.photos).toBe(0)
    expect(spawnMock).toHaveBeenCalledTimes(2)
    await backend.stop()
  }, 10_000)

  it('rejects pending requests immediately when a malformed response arrives', async () => {
    const child = new FakeChild()
    installChild(child)
    const backend = new PythonBackend()
    const request = backend.libraryOverview()
    await waitForRequest(child)

    child.stdout.write('not-json\n')

    await expect(request).rejects.toThrow('invalid response')
  })

  it('rejects a request when writing to the backend fails', async () => {
    const child = new FakeChild()
    installChild(child)
    const backend = new PythonBackend()
    const request = backend.libraryOverview()
    await waitForRequest(child)

    child.writeError = new Error('stdin failed')
    const nextRequest = backend.libraryOverview()

    await expect(nextRequest).rejects.toThrow('communication failed')
    expect(child.kill).toHaveBeenCalled()
    await expect(request).rejects.toThrow('communication failed')
  })

  it('redacts local paths from backend errors returned to the renderer', async () => {
    const child = new FakeChild()
    installChild(child)
    const backend = new PythonBackend()
    const request = backend.libraryOverview()
    await waitForRequest(child)
    const id = (JSON.parse(child.requests[0]) as { id: string }).id
    child.stdout.write(`${JSON.stringify({
      id,
      ok: false,
      error: "Cannot read 'C:\\Users\\Alice Smith\\Photos\\private.jpg': access denied"
    })}\n`)

    await expect(request).rejects.toThrow("Cannot read '[local path]': access denied")
  })

  it('rejects pending requests and stops the child cleanly during shutdown', async () => {
    const child = new FakeChild()
    installChild(child)
    const backend = new PythonBackend()
    const request = backend.libraryOverview()
    await waitForRequest(child)

    const shutdown = backend.stop()
    expect(backend.stop()).toBe(shutdown)
    await expect(request).rejects.toThrow('stopped')
    await shutdown

    expect(child.stdin.writableEnded).toBe(true)
    expect(child.kill).not.toHaveBeenCalled()
  })
})
