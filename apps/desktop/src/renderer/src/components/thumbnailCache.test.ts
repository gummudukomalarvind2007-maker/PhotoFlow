import { describe, expect, it } from 'vitest'
import { ThumbnailCache } from './thumbnailCache'

describe('thumbnail cache', () => {
  it('shares in-flight loads and retains only the most recently used thumbnails', async () => {
    const cache = new ThumbnailCache(2)
    let resolveFirst!: (value: string | null) => void
    let firstLoads = 0
    const first = cache.load(1, () => {
      firstLoads += 1
      return new Promise<string | null>((resolve) => { resolveFirst = resolve })
    })
    const duplicate = cache.load(1, async () => {
      firstLoads += 1
      return 'unexpected'
    })

    await Promise.resolve()
    expect(firstLoads).toBe(1)
    resolveFirst('data:image/jpeg;base64,one')
    await expect(first).resolves.toBe('data:image/jpeg;base64,one')
    await expect(duplicate).resolves.toBe('data:image/jpeg;base64,one')

    await cache.load(2, async () => 'data:image/jpeg;base64,two')
    expect(cache.get(1)).toBe('data:image/jpeg;base64,one')
    await cache.load(3, async () => 'data:image/jpeg;base64,three')

    expect(cache.get(1)).toBe('data:image/jpeg;base64,one')
    expect(cache.get(2)).toBeUndefined()
    expect(cache.get(3)).toBe('data:image/jpeg;base64,three')
  })

  it('does not cache failed or missing thumbnail results', async () => {
    const cache = new ThumbnailCache(1)
    await expect(cache.load(8, async () => { throw new Error('temporary failure') })).rejects.toThrow('temporary failure')
    await expect(cache.load(8, async () => null)).resolves.toBeNull()
    await expect(cache.load(8, async () => 'data:image/jpeg;base64,recovered')).resolves.toBe('data:image/jpeg;base64,recovered')
  })

  it('limits concurrent thumbnail requests', async () => {
    const cache = new ThumbnailCache(20, 2)
    let active = 0
    let peak = 0
    const requests = Array.from({ length: 6 }, (_, index) => cache.load(index + 1, () => {
      active += 1
      peak = Math.max(peak, active)
      return new Promise<string | null>((resolve) => setTimeout(() => {
        active -= 1
        resolve(`thumbnail-${index}`)
      }, 1))
    }))

    await Promise.all(requests)
    expect(peak).toBeLessThanOrEqual(2)
  })

  it('does not restore a thumbnail when the cache is cleared during a pending load', async () => {
    const cache = new ThumbnailCache(1)
    let resolveLoad!: (value: string | null) => void
    const pending = cache.load(9, () => new Promise<string | null>((resolve) => { resolveLoad = resolve }))
    await Promise.resolve()
    cache.clear()
    resolveLoad('data:image/jpeg;base64,stale')
    await pending

    expect(cache.get(9)).toBeUndefined()
  })
})
