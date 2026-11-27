export type MediaSelection =
  | { kind: 'explicit'; ids: number[]; scope: string }
  | { kind: 'all'; excludedIds: number[]; scope: string }

export const EMPTY_MEDIA_SELECTION: MediaSelection = { kind: 'explicit', ids: [], scope: '' }

export function selectAllMedia(scope: string): MediaSelection {
  return { kind: 'all', excludedIds: [], scope }
}

export function clearMediaSelection(): MediaSelection {
  return EMPTY_MEDIA_SELECTION
}

export function mediaSelectionForScope(selection: MediaSelection, scope: string): MediaSelection {
  return selection.scope === scope ? selection : { kind: 'explicit', ids: [], scope }
}

export function toggleMediaSelection(selection: MediaSelection, mediaId: number, checked: boolean, scope: string): MediaSelection {
  const scopedSelection = mediaSelectionForScope(selection, scope)
  if (scopedSelection.kind === 'all') {
    const excluded = new Set(scopedSelection.excludedIds)
    if (checked) excluded.delete(mediaId)
    else excluded.add(mediaId)
    return { kind: 'all', excludedIds: [...excluded], scope }
  }
  const ids = new Set(scopedSelection.ids)
  if (checked) ids.add(mediaId)
  else ids.delete(mediaId)
  return { kind: 'explicit', ids: [...ids], scope }
}

export function isMediaSelected(selection: MediaSelection, mediaId: number, scope: string): boolean {
  const scopedSelection = mediaSelectionForScope(selection, scope)
  return scopedSelection.kind === 'all'
    ? !scopedSelection.excludedIds.includes(mediaId)
    : scopedSelection.ids.includes(mediaId)
}

export function mediaSelectionCount(selection: MediaSelection, matchingCount: number, scope: string): number {
  const scopedSelection = mediaSelectionForScope(selection, scope)
  return scopedSelection.kind === 'all'
    ? Math.max(0, matchingCount - scopedSelection.excludedIds.length)
    : scopedSelection.ids.length
}
