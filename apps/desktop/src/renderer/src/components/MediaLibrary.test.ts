import { describe, expect, it } from 'vitest'
import { metadataStatusLabel } from './metadataStatus'
import {
  clearMediaSelection,
  isMediaSelected,
  mediaSelectionForScope,
  mediaSelectionCount,
  selectAllMedia,
  toggleMediaSelection
} from './mediaSelection'

describe('metadata status labels', () => {
  it('distinguishes extracted, partial, unavailable, and failed metadata', () => {
    const item = { id: 1, media_type: 'photo' as const, extension: '.jpg', filename: 'photo.jpg', size_bytes: 10 }
    expect(metadataStatusLabel({ ...item, metadata_status: 'extracted' })).toBe('Metadata extracted')
    expect(metadataStatusLabel({ ...item, metadata_status: 'partial' })).toBe('Some metadata unavailable')
    expect(metadataStatusLabel({ ...item, metadata_status: 'unavailable' })).toBe('Metadata unavailable')
    expect(metadataStatusLabel({ ...item, metadata_status: 'failed' })).toBe('Metadata extraction failed')
  })

  it('never labels a legacy row with missing dimensions as pending', () => {
    const item = { id: 1, media_type: 'photo' as const, extension: '.jpg', filename: 'photo.jpg', size_bytes: 10 }
    expect(metadataStatusLabel(item)).toBe('Metadata unavailable')
    expect(metadataStatusLabel({ ...item, width: 1200, height: 800 })).toBe('Some metadata unavailable')
  })
})

describe('media selection', () => {
  const photoFilter = JSON.stringify(['photo', { extension: '.jpg', sort: 'newest' }])

  it('replaces prior individual picks with all 930 matching items across pages', () => {
    let selection = toggleMediaSelection(clearMediaSelection(), 1, true, photoFilter)
    selection = toggleMediaSelection(selection, 2, true, photoFilter)
    selection = toggleMediaSelection(selection, 3, true, photoFilter)
    expect(mediaSelectionCount(selection, 930, photoFilter)).toBe(3)

    selection = selectAllMedia(photoFilter)
    const matchingIds = Array.from({ length: 930 }, (_, index) => index + 1)
    const pages = Array.from({ length: Math.ceil(matchingIds.length / 60) }, (_, index) => matchingIds.slice(index * 60, (index + 1) * 60))
    expect(mediaSelectionCount(selection, 930, photoFilter)).toBe(930)
    expect(pages.flat().filter((mediaId) => isMediaSelected(selection, mediaId, photoFilter))).toEqual(matchingIds)

    selection = toggleMediaSelection(selection, 91, false, photoFilter)
    expect(mediaSelectionCount(selection, 930, photoFilter)).toBe(929)
    expect(isMediaSelected(selection, 91, photoFilter)).toBe(false)
    selection = toggleMediaSelection(selection, 91, true, photoFilter)
    expect(mediaSelectionCount(selection, 930, photoFilter)).toBe(930)
  })

  it('tracks explicit selections and clears explicit and select-all states', () => {
    let selection = toggleMediaSelection(clearMediaSelection(), 4, true, photoFilter)
    selection = toggleMediaSelection(selection, 9, true, photoFilter)
    expect(mediaSelectionCount(selection, 100, photoFilter)).toBe(2)
    expect(isMediaSelected(selection, 9, photoFilter)).toBe(true)
    selection = toggleMediaSelection(selection, 9, false, photoFilter)
    expect(mediaSelectionCount(selection, 100, photoFilter)).toBe(1)
    expect(isMediaSelected(selection, 9, photoFilter)).toBe(false)

    selection = toggleMediaSelection(selectAllMedia(photoFilter), 3, false, photoFilter)
    expect(mediaSelectionCount(selection, 8, photoFilter)).toBe(7)
    selection = clearMediaSelection()
    expect(mediaSelectionCount(selection, 8, photoFilter)).toBe(0)
    expect(isMediaSelected(selection, 4, photoFilter)).toBe(false)
  })

  it('clears a selection immediately when filters change', () => {
    const selection = selectAllMedia(photoFilter)
    const videoFilter = JSON.stringify(['video', { extension: '.mp4', sort: 'newest' }])
    const activeSelection = mediaSelectionForScope(selection, videoFilter)

    expect(activeSelection.kind).toBe('explicit')
    expect(mediaSelectionCount(activeSelection, 42, videoFilter)).toBe(0)
    expect(isMediaSelected(activeSelection, 1, videoFilter)).toBe(false)
  })

  it('keeps selected counts aligned with unique selected IDs', () => {
    let selection = toggleMediaSelection(clearMediaSelection(), 4, true, photoFilter)
    selection = toggleMediaSelection(selection, 4, true, photoFilter)
    selection = toggleMediaSelection(selection, 9, true, photoFilter)
    const matchingIds = [4, 9, 12]

    expect(new Set(selection.kind === 'explicit' ? selection.ids : []).size).toBe(2)
    expect(matchingIds.filter((id) => isMediaSelected(selection, id, photoFilter))).toHaveLength(mediaSelectionCount(selection, 3, photoFilter))
  })
})
