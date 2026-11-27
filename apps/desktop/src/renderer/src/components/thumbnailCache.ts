export class ThumbnailCache {
  private readonly entries = new Map<number, string>()
  private readonly pending = new Map<number, Promise<string | null>>()
  private readonly queue: Array<{
    mediaId: number
    load: () => Promise<string | null>
    generation: number
    resolve: (value: string | null) => void
    reject: (error: unknown) => void
  }> = []
  private activeRequests = 0
  private generation = 0

  constructor(private readonly capacity: number, private readonly maxPending = 4) {
    if (!Number.isSafeInteger(capacity) || capacity < 1) {
      throw new Error('Thumbnail cache capacity must be a positive integer.')
    }
    if (!Number.isSafeInteger(maxPending) || maxPending < 1) {
      throw new Error('Thumbnail cache concurrency must be a positive integer.')
    }
  }

  get(mediaId: number): string | undefined {
    const value = this.entries.get(mediaId)
    if (value === undefined) return undefined
    this.entries.delete(mediaId)
    this.entries.set(mediaId, value)
    return value
  }

  load(mediaId: number, load: () => Promise<string | null>): Promise<string | null> {
    const cached = this.get(mediaId)
    if (cached !== undefined) return Promise.resolve(cached)
    const existing = this.pending.get(mediaId)
    if (existing) return existing

    const generation = this.generation
    const request = new Promise<string | null>((resolve, reject) => {
      this.queue.push({ mediaId, load, generation, resolve, reject })
    })
    this.pending.set(mediaId, request)
    this.drainQueue()
    return request
  }

  private drainQueue(): void {
    while (this.activeRequests < this.maxPending && this.queue.length) {
      const item = this.queue.shift()
      if (!item) break
      if (item.generation !== this.generation) {
        item.resolve(null)
        continue
      }
      this.activeRequests += 1
      void Promise.resolve()
        .then(item.load)
        .then((value) => {
          if (value && item.generation === this.generation) {
            this.entries.set(item.mediaId, value)
            while (this.entries.size > this.capacity) {
              const oldest = this.entries.keys().next().value
              if (oldest === undefined) break
              this.entries.delete(oldest)
            }
          }
          item.resolve(value)
        })
        .catch(item.reject)
        .finally(() => {
          this.activeRequests -= 1
          if (this.pending.get(item.mediaId) === this.pending.get(item.mediaId)) {
            this.pending.delete(item.mediaId)
          }
          this.drainQueue()
        })
    }
  }

  clear(): void {
    this.generation += 1
    this.entries.clear()
    for (const item of this.queue) item.resolve(null)
    this.queue.length = 0
    this.pending.clear()
  }
}

export const thumbnailCache = new ThumbnailCache(120)
