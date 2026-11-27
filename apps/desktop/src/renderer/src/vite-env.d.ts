/// <reference types="vite/client" />

interface Window {
  photoFlow: {
    getVersion: () => Promise<string>
    getBackendHealth: () => Promise<{
      status: 'ok' | 'unavailable'
      pythonVersion?: string
      sqliteVersion?: string
      databasePath?: string
      message?: string
    }>
    selectAndScan: () => Promise<{ cancelled: boolean; scan?: ScanStatus }>
    getScanStatus: (scanId: string) => Promise<ScanStatus>
    cancelScan: (scanId: string) => Promise<ScanStatus>
    resumeScan: (scanId: string) => Promise<ScanStatus>
    retryFailedScan: (scanId: string) => Promise<ScanStatus>
    getResumableScan: () => Promise<ScanStatus | null>
    getScanHistory: () => Promise<ScanStatus[]>
    getFailedItems: () => Promise<FailedItems>
    getLibraryOverview: () => Promise<LibraryOverview>
    listMedia: (mediaType: 'all' | 'photo' | 'video', limit?: number, offset?: number, filters?: MediaFilters) => Promise<MediaItem[]>
    countMedia: (mediaType: 'all' | 'photo' | 'video', filters?: MediaFilters) => Promise<number>
    removeMedia: (selection: MediaRemovalSelection) => Promise<MediaRemovalResult>
    getThumbnail: (mediaId: number) => Promise<string | null>
    getFullResolution: (mediaId: number) => Promise<string>
    listAlbums: () => Promise<Album[]>
    createAlbum: (name: string, description?: string) => Promise<Album>
    renameAlbum: (albumId: number, name: string, description?: string) => Promise<Album>
    deleteAlbum: (albumId: number) => Promise<void>
    addAlbumMedia: (albumId: number, mediaIds: number[]) => Promise<number>
    removeAlbumMedia: (albumId: number, mediaIds: number[]) => Promise<number>
    listAlbumMedia: (albumId: number, limit?: number, offset?: number) => Promise<MediaItem[]>
    listSmartAlbums: () => Promise<SmartAlbum[]>
    listSmartAlbumMedia: (kind: SmartAlbumKind, value: string, limit?: number, offset?: number) => Promise<MediaItem[]>
    listSmartCollections: () => Promise<SmartCollection[]>
    listSmartCollectionMedia: (kind: SmartCollectionKind, value: string, limit?: number, offset?: number) => Promise<MediaItem[]>
    listSavedSmartAlbums: () => Promise<SavedSmartAlbum[]>
    createSavedSmartAlbum: (name: string, description: string, mediaType: 'all' | 'photo' | 'video', filters: MediaFilters) => Promise<SavedSmartAlbum>
    deleteSavedSmartAlbum: (albumId: number) => Promise<void>
    listSavedSmartAlbumMedia: (albumId: number, limit?: number, offset?: number) => Promise<MediaItem[]>
    listOccasions: (groupBy: OccasionGrouping, dateFrom?: string, dateTo?: string, limit?: number, offset?: number) => Promise<Occasion[]>
    listOccasionMedia: (groupBy: OccasionGrouping, period: string, dateSource: OccasionDateSource, limit?: number, offset?: number) => Promise<MediaItem[]>
    exportSelectedMedia: (mediaIds: number[], organizeByType: boolean) => Promise<TransferStartResult>
    exportAlbum: (albumId: number, organizeByType: boolean) => Promise<TransferStartResult>
    exportFilteredMedia: (mediaType: 'all' | 'photo' | 'video', filters: MediaFilters, organizeByType: boolean) => Promise<TransferStartResult>
    exportMetadata: (format: 'csv' | 'json') => Promise<TransferStartResult>
    createDatabaseBackup: () => Promise<TransferStartResult>
    restoreDatabaseBackup: () => Promise<TransferStartResult>
    getApplicationSettings: () => Promise<ApplicationSettings>
    setSkipUnchangedFiles: (value: boolean) => Promise<boolean>
    clearThumbnailCache: () => Promise<TransferStartResult>
    getTransferStatus: (jobId: string) => Promise<TransferJob>
    cancelTransfer: (jobId: string) => Promise<TransferJob>
    startPhotoReview: (similarityThreshold?: number) => Promise<PhotoReviewJob>
    retryFailedPhotoAnalysis: () => Promise<PhotoReviewJob>
    getPhotoReviewStatus: (jobId?: string) => Promise<PhotoReviewJob | null>
    cancelPhotoReview: (jobId: string) => Promise<PhotoReviewJob>
    getPhotoReviewOverview: (similarityThreshold?: number) => Promise<Record<PhotoReviewCategory, number>>
    listPhotoReview: (category: PhotoReviewCategory, status: PhotoReviewStatus, limit?: number, offset?: number, similarityThreshold?: number) => Promise<PhotoReviewResult>
    setPhotoReviewStatus: (mediaId: number, status: PhotoReviewMark) => Promise<void>
  }
}

interface ScanStatus {
  id: string
  status: 'running' | 'completed' | 'cancelled' | 'interrupted' | 'failed'
  discovered_files: number
  processed_files: number
  indexed_files: number
  skipped_files: number
  failed_files: number
  error_summary?: string | null
  cancel_requested: boolean
  started_at?: string
  completed_at?: string | null
}

interface LibraryOverview { photos: number; videos: number; albums: number; total_bytes: number }
interface MediaItem { id: number; media_type: 'photo' | 'video'; extension: string; filename: string; size_bytes: number; width?: number | null; height?: number | null; duration_seconds?: number | null; frame_rate?: number | null; capture_date?: string | null; camera_make?: string | null; camera_model?: string | null; metadata_status?: 'extracted' | 'partial' | 'unavailable' | 'failed'; metadata_error?: string | null }
type MediaRemovalSelection =
  | { mode: 'ids'; media_ids: number[] }
  | { mode: 'filtered'; media_type: 'all' | 'photo' | 'video'; filters: MediaFilters; excluded_ids: number[] }
interface MediaRemovalResult { removed: number; thumbnails_deleted: number; thumbnail_cleanup_failed: number }
interface RetryFailure { id: number | string; scan_id?: string; media_id?: number; kind: 'scan-item' | 'scan-job' | 'photo-analysis' | 'photo-analysis-job'; filename: string; media_type?: 'photo' | 'video' | null; error_summary: string; created_at?: string }
interface FailedItems { scan_failures: RetryFailure[]; analysis_failures: RetryFailure[] }
interface Album { id: number; name: string; description: string; created_at: string; updated_at: string; media_count: number; cover_media_id: number | null }
type SmartAlbumKind = 'year' | 'month' | 'camera' | 'media_type' | 'recent' | 'videos'
interface SmartAlbum { kind: SmartAlbumKind; value: string; name: string; media_count: number; cover_media_id: number | null }
type SmartCollectionKind = 'recently_added' | 'recently_viewed' | 'screenshots' | 'portrait' | 'landscape' | 'high_resolution' | 'large_files' | 'photo_year' | 'photo_month' | 'similar_dimensions' | 'video_duration'
interface SmartCollection { kind: SmartCollectionKind; value: string; name: string; description: string; media_count: number; cover_media_id: number | null }
interface SavedSmartAlbum { id: number; name: string; description: string; media_type: 'all' | 'photo' | 'video'; filters: MediaFilters; created_at: string; updated_at: string; media_count: number; cover_media_id: number | null }
type OccasionGrouping = 'year' | 'month' | 'day'
type OccasionDateSource = 'captured' | 'indexed'
interface Occasion { period: string; date_source: OccasionDateSource; media_count: number; cover_media_id: number }
interface TransferJob {
  id: string
  operation: 'media-export' | 'metadata-export' | 'backup' | 'restore' | 'cache-clear'
  status: 'running' | 'completed' | 'cancelled' | 'interrupted' | 'failed'
  total: number
  completed: number
  succeeded: number
  failed: number
  cancel_requested: boolean
  error_summary?: string | null
}
interface TransferStartResult { cancelled: boolean; job?: TransferJob }
interface ApplicationSettings {
  application_data_path: string
  database_path: string
  thumbnail_cache_path: string
  photo_count: number
  video_count: number
  library_size_bytes: number
  thumbnail_cache_files: number
  thumbnail_cache_bytes: number
  python_version: string
  ffmpeg_available: boolean
  ffprobe_available: boolean
  theme: string
  theme_customization_supported: boolean
  skip_unchanged_files: boolean
  application_version: string
}
interface MediaFilters {
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
type PhotoReviewCategory = 'exact' | 'similar' | 'blurry' | 'low_resolution' | 'exposure'
type PhotoReviewStatus = 'pending' | 'later' | 'reviewed' | 'all'
type PhotoReviewMark = Exclude<PhotoReviewStatus, 'all'>
interface PhotoReviewJob {
  id: string
  status: 'running' | 'completed' | 'cancelled' | 'interrupted' | 'failed'
  similarity_threshold: number
  total_files: number
  processed_files: number
  analyzed_files: number
  failed_files: number
  cancel_requested: boolean
  error_summary?: string | null
}
interface ReviewMediaItem extends MediaItem {
  location: string
  review_status: PhotoReviewMark
  is_representative?: boolean
  issues?: string[]
  reason?: string
  laplacian_variance?: number | null
  mean_luminance?: number | null
  dark_pixel_percent?: number | null
  bright_pixel_percent?: number | null
}
interface PhotoReviewGroup {
  id: number
  item_count?: number
  representative_id?: number
  distance?: number
  explanation: string
  items: ReviewMediaItem[]
}
interface PhotoReviewResult {
  groups: PhotoReviewGroup[]
  items: ReviewMediaItem[]
}

