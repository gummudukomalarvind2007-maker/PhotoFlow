import { contextBridge, ipcRenderer } from 'electron'
import type {
  Album,
  ApplicationSettings,
  FailedItems,
  Occasion,
  OccasionGrouping,
  PhotoReviewCategory,
  PhotoReviewJob,
  PhotoReviewMark,
  PhotoReviewResult,
  PhotoReviewStatus,
  SavedSmartAlbum,
  ScanStatus,
  SmartAlbum,
  SmartAlbumKind,
  SmartCollection,
  SmartCollectionKind,
  MediaItem,
  MediaRemovalResult,
  MediaRemovalSelection,
  TransferJob
} from '../main/pythonBackend'

type MediaFilters = {
  search?: string
  extension?: string
  date_from?: string
  date_to?: string
  camera_make?: string
  camera_model?: string
  min_width?: number
  max_width?: number
  min_height?: number
  max_height?: number
  min_resolution_px?: number
  max_resolution_px?: number
  min_duration_seconds?: number
  max_duration_seconds?: number
  min_size_bytes?: number
  max_size_bytes?: number
  sort?: 'newest' | 'oldest' | 'filename-asc' | 'filename-desc' | 'size-desc' | 'size-asc'
}

contextBridge.exposeInMainWorld('photoFlow', {
  getVersion: (): Promise<string> => ipcRenderer.invoke('app:version'),
  getBackendHealth: () => ipcRenderer.invoke('backend:health'),
  selectAndScan: () => ipcRenderer.invoke('library:select-and-scan'),
  getScanStatus: (scanId: string) => ipcRenderer.invoke('library:scan-status', scanId),
  cancelScan: (scanId: string) => ipcRenderer.invoke('library:cancel-scan', scanId),
  resumeScan: (scanId: string) => ipcRenderer.invoke('library:resume-scan', scanId),
  retryFailedScan: (scanId: string) => ipcRenderer.invoke('library:retry-failed-scan', scanId),
  getScanHistory: (): Promise<ScanStatus[]> => ipcRenderer.invoke('library:scan-history'),
  getFailedItems: (): Promise<FailedItems> => ipcRenderer.invoke('library:failed-items'),
  getResumableScan: () => ipcRenderer.invoke('library:resumable-scan'),
  getLibraryOverview: () => ipcRenderer.invoke('library:overview'),
  listMedia: (mediaType: 'all' | 'photo' | 'video', limit = 60, offset = 0, filters: MediaFilters = {}) =>
    ipcRenderer.invoke('library:list-media', mediaType, limit, offset, filters),
  countMedia: (mediaType: 'all' | 'photo' | 'video', filters: MediaFilters = {}): Promise<number> =>
    ipcRenderer.invoke('library:count-media', mediaType, filters),
  removeMedia: (selection: MediaRemovalSelection): Promise<MediaRemovalResult> =>
    ipcRenderer.invoke('library:remove-media', selection),
  getThumbnail: (mediaId: number) => ipcRenderer.invoke('library:thumbnail', mediaId),
  getFullResolution: (mediaId: number): Promise<string> => ipcRenderer.invoke('library:full-resolution', mediaId),
  exportSelectedMedia: (mediaIds: number[], organizeByType: boolean) =>
    ipcRenderer.invoke('export:media-selected', mediaIds, organizeByType) as Promise<{ cancelled: boolean; job?: TransferJob }>,
  exportAlbum: (albumId: number, organizeByType: boolean) =>
    ipcRenderer.invoke('export:album', albumId, organizeByType) as Promise<{ cancelled: boolean; job?: TransferJob }>,
  exportFilteredMedia: (mediaType: 'all' | 'photo' | 'video', filters: MediaFilters, organizeByType: boolean) =>
    ipcRenderer.invoke('export:filtered', mediaType, filters, organizeByType) as Promise<{ cancelled: boolean; job?: TransferJob }>,
  exportMetadata: (format: 'csv' | 'json') =>
    ipcRenderer.invoke('export:metadata', format) as Promise<{ cancelled: boolean; job?: TransferJob }>,
  createDatabaseBackup: () =>
    ipcRenderer.invoke('backup:create') as Promise<{ cancelled: boolean; job?: TransferJob }>,
  restoreDatabaseBackup: () =>
    ipcRenderer.invoke('backup:restore') as Promise<{ cancelled: boolean; job?: TransferJob }>,
  getApplicationSettings: (): Promise<ApplicationSettings> => ipcRenderer.invoke('settings:overview'),
  setSkipUnchangedFiles: (value: boolean): Promise<boolean> => ipcRenderer.invoke('settings:scan-preference', value),
  clearThumbnailCache: () =>
    ipcRenderer.invoke('settings:clear-cache') as Promise<{ cancelled: boolean; job?: TransferJob }>,
  getTransferStatus: (jobId: string): Promise<TransferJob> => ipcRenderer.invoke('transfer:status', jobId),
  cancelTransfer: (jobId: string): Promise<TransferJob> => ipcRenderer.invoke('transfer:cancel', jobId),
  listAlbums: (): Promise<Album[]> => ipcRenderer.invoke('albums:list'),
  createAlbum: (name: string, description = ''): Promise<Album> => ipcRenderer.invoke('albums:create', name, description),
  renameAlbum: (albumId: number, name: string, description = ''): Promise<Album> =>
    ipcRenderer.invoke('albums:rename', albumId, name, description),
  deleteAlbum: (albumId: number): Promise<void> => ipcRenderer.invoke('albums:delete', albumId),
  addAlbumMedia: (albumId: number, mediaIds: number[]): Promise<number> =>
    ipcRenderer.invoke('albums:add-media', albumId, mediaIds),
  removeAlbumMedia: (albumId: number, mediaIds: number[]): Promise<number> =>
    ipcRenderer.invoke('albums:remove-media', albumId, mediaIds),
  listAlbumMedia: (albumId: number, limit = 60, offset = 0): Promise<MediaItem[]> =>
    ipcRenderer.invoke('albums:media', albumId, limit, offset),
  listSmartAlbums: (): Promise<SmartAlbum[]> => ipcRenderer.invoke('smart-albums:list'),
  listSmartAlbumMedia: (kind: SmartAlbumKind, value: string, limit = 60, offset = 0): Promise<MediaItem[]> =>
    ipcRenderer.invoke('smart-albums:media', kind, value, limit, offset),
  listSmartCollections: (): Promise<SmartCollection[]> => ipcRenderer.invoke('smart-collections:list'),
  listSmartCollectionMedia: (kind: SmartCollectionKind, value: string, limit = 60, offset = 0): Promise<MediaItem[]> =>
    ipcRenderer.invoke('smart-collections:media', kind, value, limit, offset),
  listSavedSmartAlbums: (): Promise<SavedSmartAlbum[]> => ipcRenderer.invoke('saved-smart-albums:list'),
  createSavedSmartAlbum: (
    name: string, description: string, mediaType: 'all' | 'photo' | 'video', filters: MediaFilters
  ): Promise<SavedSmartAlbum> => ipcRenderer.invoke('saved-smart-albums:create', name, description, mediaType, filters),
  deleteSavedSmartAlbum: (albumId: number): Promise<void> => ipcRenderer.invoke('saved-smart-albums:delete', albumId),
  listSavedSmartAlbumMedia: (albumId: number, limit = 60, offset = 0): Promise<MediaItem[]> =>
    ipcRenderer.invoke('saved-smart-albums:media', albumId, limit, offset),
  listOccasions: (
    groupBy: OccasionGrouping,
    dateFrom?: string,
    dateTo?: string,
    limit = 100,
    offset = 0
  ): Promise<Occasion[]> => ipcRenderer.invoke('occasions:list', groupBy, dateFrom, dateTo, limit, offset),
  listOccasionMedia: (
    groupBy: OccasionGrouping,
    period: string,
    dateSource: Occasion['date_source'],
    limit = 60,
    offset = 0
  ): Promise<MediaItem[]> => ipcRenderer.invoke('occasions:media', groupBy, period, dateSource, limit, offset),
  startPhotoReview: (similarityThreshold = 6): Promise<PhotoReviewJob> =>
    ipcRenderer.invoke('photo-review:start', similarityThreshold),
  retryFailedPhotoAnalysis: (): Promise<PhotoReviewJob> => ipcRenderer.invoke('photo-review:retry-failed'),
  getPhotoReviewStatus: (jobId?: string): Promise<PhotoReviewJob | null> =>
    ipcRenderer.invoke('photo-review:status', jobId),
  cancelPhotoReview: (jobId: string): Promise<PhotoReviewJob> =>
    ipcRenderer.invoke('photo-review:cancel', jobId),
  getPhotoReviewOverview: (similarityThreshold = 6): Promise<Record<PhotoReviewCategory, number>> =>
    ipcRenderer.invoke('photo-review:overview', similarityThreshold),
  listPhotoReview: (
    category: PhotoReviewCategory,
    status: PhotoReviewStatus,
    limit = 30,
    offset = 0,
    similarityThreshold = 6
  ): Promise<PhotoReviewResult> =>
    ipcRenderer.invoke('photo-review:list', category, status, limit, offset, similarityThreshold),
  setPhotoReviewStatus: (mediaId: number, status: PhotoReviewMark): Promise<void> =>
    ipcRenderer.invoke('photo-review:set-status', mediaId, status)
})

