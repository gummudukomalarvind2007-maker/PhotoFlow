import type { NavigationItem } from './types'

export const navigation: Array<{ label: NavigationItem; icon: string; badge?: string }> = [
  { label: 'Dashboard', icon: 'grid' }, { label: 'Photos', icon: 'image' }, { label: 'Videos', icon: 'video' },
  { label: 'Smart Collections', icon: 'sparkle' },
  { label: 'Albums', icon: 'folder' }, { label: 'Occasions', icon: 'calendar' },
  { label: 'Quality Review', icon: 'sparkle' }, { label: 'Duplicates', icon: 'copy' },
  { label: 'Retry Failed', icon: 'rotate' },
  { label: 'Export', icon: 'arrow-up' }, { label: 'Settings', icon: 'settings' }
]
