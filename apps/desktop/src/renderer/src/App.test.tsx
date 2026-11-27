import { describe, expect, it } from 'vitest'
import { navigation } from './data'

describe('PhotoFlow navigation', () => {
  it('includes every Phase 2 workspace', () => {
    expect(navigation.map((item) => item.label)).toEqual([
      'Dashboard', 'Photos', 'Videos', 'Smart Collections', 'Albums', 'Occasions',
      'Quality Review', 'Duplicates', 'Retry Failed', 'Export', 'Settings'
    ])
  })

  it('does not ship a sample failure count in navigation', () => {
    expect(navigation.find((item) => item.label === 'Retry Failed')?.badge).toBeUndefined()
  })
})

