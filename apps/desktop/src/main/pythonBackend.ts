import { ChildProcessWithoutNullStreams, spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { delimiter, join } from 'node:path'
import { app } from 'electron'
import { logger } from './logger'

export interface BackendHealthResult {
  status: 'ok' | 'unavailable'
  pythonVersion?: string
  sqliteVersion?: string
  message?: string
}

export interface ScanStatus {
  id: string
  status: 'running' | 'completed' | 'cancelled' | 'interrupted' | 'failed'
  discovered_files: number
  processed_files: number
  indexed_files: number
  skipped_files: number
  failed_files: number
  error_summary?: string | null
  cancel_requested: boolean
}

export interface LibraryOverview {
  photos: number
  videos: number
  albums: number
  total_bytes: number
}

export interface MediaItem {
  id: number
  media_type: 'photo' | 'video'
  extension: string
  filename: string
  size_bytes: number
  width?: number | null
  height?: number | null
  duration_seconds?: number | null
  frame_rate?: number | null
  capture_date?: string | null
  camera_make?: string | null
  camera_model?: string | null
  metadata_status?: 'extracted' | 'partial' | 'unavailable' | 'failed'
  metadata_error?: string | null
}

export interface RetryFailure {
  id: number | string
  scan_id?: string
  media_id?: number
  kind: 'scan-item' | 'scan-job' | 'photo-analysis' | 'photo-analysis-job'
  filename: string
  media_type?: 'photo' | 'video' | null
  error_summary: string
  created_at?: string
}

export interface FailedItems {
  scan_failures: RetryFailure[]
  analysis_failures: RetryFailure[]
}

export interface Album {
  id: number
  name: string
  description: string
  created_at: string
  updated_at: string
  media_count: number
  cover_media_id: number | null
}

export type SmartAlbumKind = 'year' | 'month' | 'camera' | 'media_type' | 'recent' | 'videos'
export type SmartCollectionKind =
  | 'recently_added'
  | 'recently_viewed'
  | 'screenshots'
  | 'portrait'
  | 'landscape'
  | 'high_resolution'
  | 'large_files'
  | 'photo_year'
  | 'photo_month'
  | 'similar_dimensions'
  | 'video_duration'
export interface SmartAlbum {
  kind: SmartAlbumKind
  value: string
  name: string
  media_count: number
  cover_media_id: number | null
}

export interface SmartCollection {
  kind: SmartCollectionKind
  value: string
  name: string
  description: string
  media_count: number
  cover_media_id: number | null
}

export interface SavedSmartAlbum {
  id: number
  name: string
  description: string
  media_type: 'all' | 'photo' | 'video'
  filters: MediaFilters
  created_at: string
  updated_at: string
  media_count: number
  cover_media_id: number | null
}

export interface Occasion {
  period: string
  date_source: 'captured' | 'indexed'
  media_count: number
  cover_media_id: number
}

export type OccasionGrouping = 'year' | 'month' | 'day'

export interface TransferJob {
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

export interface ApplicationSettings {
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

export interface MediaFilters {
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

export type MediaRemovalSelection =
  | { mode: 'ids'; media_ids: number[] }
  | { mode: 'filtered'; media_type: 'all' | 'photo' | 'video'; filters: MediaFilters; excluded_ids: number[] }

export interface MediaRemovalResult {
  removed: number
  thumbnails_deleted: number
  thumbnail_cleanup_failed: number
}

export type PhotoReviewCategory = 'exact' | 'similar' | 'blurry' | 'low_resolution' | 'exposure'
export type PhotoReviewStatus = 'pending' | 'later' | 'reviewed' | 'all'
export type PhotoReviewMark = Exclude<PhotoReviewStatus, 'all'>

export interface PhotoReviewJob {
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

export interface ReviewMediaItem extends MediaItem {
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

export interface PhotoReviewGroup {
  id: number
  item_count?: number
  representative_id?: number
  distance?: number
  explanation: string
  items: ReviewMediaItem[]
}

export interface PhotoReviewResult {
  groups: PhotoReviewGroup[]
  items: ReviewMediaItem[]
}

interface PendingRequest {
  resolve: (value: Record<string, unknown>) => void
  reject: (reason: Error) => void
  timeout: NodeJS.Timeout
}

function safeErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  return message
    .replace(/(['"])(?:[A-Za-z]:\\|\\\\|\/)[^'"]*\1/g, '$1[local path]$1')
    .replace(/(?:[A-Za-z]:\\|\\\\)[^\s"'<>;,]*/g, '[local path]')
    .replace(/(^|[\s("'=:])\/(?:[^/\s"'<>]+\/)*[^/\s"'<>]*/g, '$1[local path]')
}

/** A narrow JSON Lines client; it exposes only backend commands, never filesystem APIs. */
export class PythonBackend {
  private process?: ChildProcessWithoutNullStreams
  private startupChild?: ChildProcessWithoutNullStreams
  private startupPromise?: Promise<void>
  private stopPromise?: Promise<void>
  private stopping = false
  private readonly pending = new Map<string, PendingRequest>()
  private buffer = ''
  private requestNumber = 0

  private get backendDirectory(): string {
    return join(app.getAppPath(), '..', '..', 'services', 'backend')
  }

  private get entrypoint(): string {
    return join(this.backendDirectory, 'run_backend.py')
  }

  async health(): Promise<BackendHealthResult> {
    try {
      await this.ensureRunning()
      const response = await this.request('health')
      if (response.database_path) logger.info(`PhotoFlow database ${String(response.database_path)}`)
      return {
        status: 'ok',
        pythonVersion: String(response.python_version),
        sqliteVersion: String(response.sqlite_version)
      }
    } catch (error) {
      logger.warn('Python backend health check unavailable', error)
      return { status: 'unavailable', message: safeErrorMessage(error) }
    }
  }

  async startScan(folders: string[]): Promise<ScanStatus> {
    const response = await this.command('scan:start', { folders })
    return response.scan as unknown as ScanStatus
  }

  async scanStatus(scanId: string): Promise<ScanStatus> {
    const response = await this.command('scan:status', { scan_id: scanId })
    return response.scan as unknown as ScanStatus
  }

  async cancelScan(scanId: string): Promise<ScanStatus> {
    const response = await this.command('scan:cancel', { scan_id: scanId })
    return response.scan as unknown as ScanStatus
  }

  async resumeScan(scanId: string): Promise<ScanStatus> {
    const response = await this.command('scan:resume', { scan_id: scanId })
    return response.scan as unknown as ScanStatus
  }

  async retryFailedScan(scanId: string): Promise<ScanStatus> {
    const response = await this.command('scan:retry-failed', { scan_id: scanId })
    return response.scan as unknown as ScanStatus
  }

  async scanHistory(): Promise<ScanStatus[]> {
    const response = await this.command('scan:history')
    return response.scans as unknown as ScanStatus[]
  }

  async failedItems(): Promise<FailedItems> {
    const response = await this.command('retry:failed-list')
    return response as unknown as FailedItems
  }

  async retryFailedPhotoAnalysis(): Promise<PhotoReviewJob> {
    const response = await this.command('photo-review:retry-failed')
    return response.job as unknown as PhotoReviewJob
  }

  async resumableScan(): Promise<ScanStatus | null> {
    const response = await this.command('scan:resumable')
    return response.scan ? (response.scan as unknown as ScanStatus) : null
  }

  async libraryOverview(): Promise<LibraryOverview> {
    const response = await this.command('library:overview')
    return response.overview as unknown as LibraryOverview
  }

  async listMedia(mediaType: 'all' | 'photo' | 'video', limit = 60, offset = 0, filters: MediaFilters = {}): Promise<MediaItem[]> {
    const response = await this.command('media:list', { media_type: mediaType, limit, offset, ...filters }, 15_000)
    return response.items as unknown as MediaItem[]
  }

  async countMedia(mediaType: 'all' | 'photo' | 'video', filters: MediaFilters = {}): Promise<number> {
    const response = await this.command('media:count', { media_type: mediaType, ...filters }, 15_000)
    return Number(response.count)
  }

  async removeMedia(selection: MediaRemovalSelection): Promise<MediaRemovalResult> {
    const response = await this.command('media:remove-selected', { ...selection }, 120_000)
    return response as unknown as MediaRemovalResult
  }

  async thumbnail(mediaId: number): Promise<string | null> {
    const response = await this.command('media:thumbnail', { media_id: mediaId }, 20_000)
    return typeof response.data_url === 'string' ? response.data_url : null
  }

  async fullResolution(mediaId: number): Promise<string> {
    const response = await this.command('media:full-resolution', { media_id: mediaId }, 60_000)
    if (typeof response.data_url !== 'string') throw new Error('PhotoFlow could not prepare this image for viewing.')
    return response.data_url
  }

  async exportSelectedMedia(destination: string, mediaIds: number[], organizeByType: boolean): Promise<TransferJob> {
    const response = await this.command('export:media', {
      mode: 'selected', destination, media_ids: mediaIds, organize_by_type: organizeByType
    })
    return response.job as TransferJob
  }

  async exportAlbum(destination: string, albumId: number, organizeByType: boolean): Promise<TransferJob> {
    const response = await this.command('export:media', {
      mode: 'album', destination, album_id: albumId, organize_by_type: organizeByType
    })
    return response.job as TransferJob
  }

  async exportFilteredMedia(
    destination: string,
    mediaType: 'all' | 'photo' | 'video',
    filters: MediaFilters,
    organizeByType: boolean
  ): Promise<TransferJob> {
    const response = await this.command('export:media', {
      mode: 'filtered', destination, media_type: mediaType, filters, organize_by_type: organizeByType
    })
    return response.job as TransferJob
  }

  async exportMetadata(destination: string, format: 'csv' | 'json'): Promise<TransferJob> {
    const response = await this.command('export:metadata', { destination, format })
    return response.job as TransferJob
  }

  async createDatabaseBackup(destination: string): Promise<TransferJob> {
    const response = await this.command('backup:create', { destination })
    return response.job as TransferJob
  }

  async restoreDatabaseBackup(source: string): Promise<TransferJob> {
    const response = await this.command('backup:restore', { source })
    return response.job as TransferJob
  }

  async settingsOverview(): Promise<Omit<ApplicationSettings, 'application_version'>> {
    const response = await this.command('settings:overview')
    return response.settings as Omit<ApplicationSettings, 'application_version'>
  }

  async setSkipUnchangedFiles(value: boolean): Promise<boolean> {
    const response = await this.command('settings:set-scan-preference', { skip_unchanged_files: value })
    return response.skip_unchanged_files === true
  }

  async startCacheClear(): Promise<TransferJob> {
    const response = await this.command('settings:clear-cache')
    return response.job as TransferJob
  }

  async transferStatus(jobId: string): Promise<TransferJob> {
    const response = await this.command('transfer:status', { job_id: jobId })
    return response.job as TransferJob
  }

  async cancelTransfer(jobId: string): Promise<TransferJob> {
    const response = await this.command('transfer:cancel', { job_id: jobId })
    return response.job as TransferJob
  }

  async listAlbums(): Promise<Album[]> {
    const response = await this.command('albums:list')
    return response.albums as Album[]
  }

  async createAlbum(name: string, description = ''): Promise<Album> {
    const response = await this.command('albums:create', { name, description })
    return response.album as Album
  }

  async renameAlbum(albumId: number, name: string, description = ''): Promise<Album> {
    const response = await this.command('albums:rename', { album_id: albumId, name, description })
    return response.album as Album
  }

  async deleteAlbum(albumId: number): Promise<void> {
    await this.command('albums:delete', { album_id: albumId })
  }

  async addAlbumMedia(albumId: number, mediaIds: number[]): Promise<number> {
    const response = await this.command('albums:add-media', { album_id: albumId, media_ids: mediaIds })
    return Number(response.added)
  }

  async removeAlbumMedia(albumId: number, mediaIds: number[]): Promise<number> {
    const response = await this.command('albums:remove-media', { album_id: albumId, media_ids: mediaIds })
    return Number(response.removed)
  }

  async listAlbumMedia(albumId: number, limit = 60, offset = 0): Promise<MediaItem[]> {
    const response = await this.command('albums:media', { album_id: albumId, limit, offset }, 15_000)
    return response.items as MediaItem[]
  }

  async listSmartAlbums(): Promise<SmartAlbum[]> {
    const response = await this.command('smart-albums:list', {}, 15_000)
    return response.albums as SmartAlbum[]
  }

  async listSmartAlbumMedia(kind: SmartAlbumKind, value: string, limit = 60, offset = 0): Promise<MediaItem[]> {
    const response = await this.command('smart-albums:media', { kind, value, limit, offset }, 15_000)
    return response.items as MediaItem[]
  }

  async listSmartCollections(): Promise<SmartCollection[]> {
    const response = await this.command('smart-collections:list', {}, 15_000)
    return response.collections as SmartCollection[]
  }

  async listSmartCollectionMedia(kind: SmartCollectionKind, value: string, limit = 60, offset = 0): Promise<MediaItem[]> {
    const response = await this.command('smart-collections:media', { kind, value, limit, offset }, 15_000)
    return response.items as MediaItem[]
  }

  async listSavedSmartAlbums(): Promise<SavedSmartAlbum[]> {
    const response = await this.command('saved-smart-albums:list', {}, 15_000)
    return response.albums as SavedSmartAlbum[]
  }

  async createSavedSmartAlbum(
    name: string,
    description: string,
    mediaType: 'all' | 'photo' | 'video',
    filters: MediaFilters
  ): Promise<SavedSmartAlbum> {
    const response = await this.command('saved-smart-albums:create', {
      name, description, media_type: mediaType, filters
    })
    return response.album as SavedSmartAlbum
  }

  async deleteSavedSmartAlbum(albumId: number): Promise<void> {
    await this.command('saved-smart-albums:delete', { album_id: albumId })
  }

  async listSavedSmartAlbumMedia(albumId: number, limit = 60, offset = 0): Promise<MediaItem[]> {
    const response = await this.command('saved-smart-albums:media', { album_id: albumId, limit, offset }, 15_000)
    return response.items as MediaItem[]
  }

  async listOccasions(
    groupBy: OccasionGrouping,
    dateFrom?: string,
    dateTo?: string,
    limit = 100,
    offset = 0
  ): Promise<Occasion[]> {
    const response = await this.command('occasions:list', {
      group_by: groupBy, date_from: dateFrom, date_to: dateTo, limit, offset
    }, 15_000)
    return response.occasions as Occasion[]
  }

  async listOccasionMedia(
    groupBy: OccasionGrouping,
    period: string,
    dateSource: Occasion['date_source'],
    limit = 60,
    offset = 0
  ): Promise<MediaItem[]> {
    const response = await this.command('occasions:media', {
      group_by: groupBy, period, date_source: dateSource, limit, offset
    }, 15_000)
    return response.items as MediaItem[]
  }

  async startPhotoReview(similarityThreshold = 6): Promise<PhotoReviewJob> {
    const response = await this.command('photo-review:start', { similarity_threshold: similarityThreshold }, 15_000)
    return response.job as unknown as PhotoReviewJob
  }

  async photoReviewStatus(jobId?: string): Promise<PhotoReviewJob | null> {
    const response = await this.command('photo-review:status', jobId ? { job_id: jobId } : {})
    return response.job as PhotoReviewJob | null
  }

  async cancelPhotoReview(jobId: string): Promise<PhotoReviewJob> {
    const response = await this.command('photo-review:cancel', { job_id: jobId })
    return response.job as unknown as PhotoReviewJob
  }

  async photoReviewOverview(similarityThreshold = 6): Promise<Record<PhotoReviewCategory, number>> {
    const response = await this.command('photo-review:overview', { similarity_threshold: similarityThreshold })
    return response.overview as Record<PhotoReviewCategory, number>
  }

  async listPhotoReview(
    category: PhotoReviewCategory,
    status: PhotoReviewStatus,
    limit: number,
    offset: number,
    similarityThreshold: number
  ): Promise<PhotoReviewResult> {
    const response = await this.command('photo-review:list', {
      category,
      status,
      limit,
      offset,
      similarity_threshold: similarityThreshold
    }, 15_000)
    return response.result as unknown as PhotoReviewResult
  }

  async setPhotoReviewStatus(mediaId: number, status: PhotoReviewMark): Promise<void> {
    await this.command('photo-review:set-status', { media_id: mediaId, status })
  }

  stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise
    this.stopping = true
    this.rejectPending(new Error('Python backend stopped'))
    this.startupChild?.kill()
    this.stopPromise = this.stopProcess()
    return this.stopPromise
  }

  private async ensureRunning(): Promise<void> {
    if (this.stopping) throw new Error('Python backend is shutting down')
    if (this.process && !this.process.killed) return
    if (!this.startupPromise) {
      const startup = this.startProcess()
      this.startupPromise = startup
    }

    const startup = this.startupPromise
    try {
      await startup
    } finally {
      if (this.startupPromise === startup) this.startupPromise = undefined
    }
  }

  private startProcess(): Promise<void> {
    const packaged = app.isPackaged
    const executable = packaged
      ? join(process.resourcesPath, 'backend', 'photoflow-backend.exe')
      : process.env.PHOTOFLOW_PYTHON ?? 'python'
    const args = packaged ? ['--serve'] : [this.entrypoint, '--serve']
    const cwd = packaged ? app.getPath('userData') : this.backendDirectory
    const binaryDirectory = packaged
      ? join(process.resourcesPath, 'backend', 'bin')
      : join(this.backendDirectory, 'bin')
    const path = [binaryDirectory, process.env.PATH ?? ''].filter(Boolean).join(delimiter)
    if (packaged && !existsSync(executable)) {
      logger.error('Packaged Python backend executable is missing')
      return Promise.reject(new Error('PhotoFlow backend is missing. Reinstall the application.'))
    }
    if (!packaged && !existsSync(this.entrypoint)) {
      logger.error(`Backend entrypoint missing: ${this.entrypoint}`)
      return Promise.reject(new Error('PhotoFlow backend entrypoint is missing. Reinstall the application.'))
    }

    let child: ChildProcessWithoutNullStreams
    try {
      child = spawn(executable, args, {
        stdio: 'pipe',
        windowsHide: true,
        cwd,
        env: { ...process.env, PATH: path, PYTHONUNBUFFERED: '1' }
      })
    } catch (error) {
      logger.error('Unable to spawn Python backend', error)
      return Promise.reject(new Error(packaged
        ? 'Unable to start the PhotoFlow backend. Reinstall the application or check its logs.'
        : 'Unable to start the PhotoFlow backend. Verify Python is installed.'))
    }

    this.startupChild = child
    return new Promise<void>((resolve, reject) => {
      let settled = false
      const startupTimeout = setTimeout(() => {
        if (settled) return
        settled = true
        this.clearStartupChild(child)
        logger.error('Timed out starting Python backend')
        child.once('spawn', () => child.kill())
        child.kill()
        reject(new Error('Timed out starting Python backend'))
      }, 5_000)

      const failStartup = (error: Error): void => {
        if (settled) return
        settled = true
        clearTimeout(startupTimeout)
        this.clearStartupChild(child)
        logger.error('Unable to start Python backend', error)
        child.kill()
        reject(new Error(packaged
          ? 'Unable to start the PhotoFlow backend. Reinstall the application or check its logs.'
          : 'Unable to start the PhotoFlow backend. Verify Python is installed.'))
      }

      child.once('error', failStartup)
      child.once('exit', (code) => {
        if (settled) return
        failStartup(new Error(`Python backend exited during startup with code ${code ?? 'unknown'}`))
      })
      child.once('spawn', () => {
        if (settled || this.stopping) {
          child.kill()
          if (!settled) {
            settled = true
            clearTimeout(startupTimeout)
            this.clearStartupChild(child)
            reject(new Error('Python backend is shutting down'))
          }
          return
        }
        settled = true
        clearTimeout(startupTimeout)
        this.clearStartupChild(child)
        this.buffer = ''
        this.process = child
        this.attach(child)
        logger.info('Python backend started')
        resolve()
      })
    })
  }

  private async command(command: string, payload: Record<string, unknown> = {}, timeoutMs = 5_000): Promise<Record<string, unknown>> {
    try {
      await this.ensureRunning()
      return await this.request(command, payload, timeoutMs)
    } catch (error) {
      throw new Error(safeErrorMessage(error))
    }
  }

  private request(command: string, payload: Record<string, unknown> = {}, timeoutMs = 5_000): Promise<Record<string, unknown>> {
    if (!this.process) return Promise.reject(new Error('Python backend is not running'))
    if (this.stopping) return Promise.reject(new Error('Python backend is shutting down'))
    const child = this.process
    const id = String(++this.requestNumber)
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`Backend request timed out: ${command}`))
      }, timeoutMs)
      this.pending.set(id, { resolve, reject, timeout })
      try {
        child.stdin.write(`${JSON.stringify({ id, command, ...payload })}\n`, (error) => {
          if (error) this.failProcess(child, error)
        })
      } catch (error) {
        this.failProcess(child, error)
      }
    })
  }

  private attach(child: ChildProcessWithoutNullStreams): void {
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => this.consume(child, chunk))
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk: string) =>
      logger.warn(`Python backend stderr: ${safeErrorMessage(chunk.trim()).slice(0, 500)}`)
    )
    child.stdin.on('error', (error) => this.failProcess(child, error))
    child.on('error', (error) => this.failProcess(child, error))
    child.on('exit', (code) => {
      if (this.process !== child) return
      this.process = undefined
      this.buffer = ''
      const reason = new Error(`Python backend exited unexpectedly with code ${code ?? 'unknown'}`)
      this.rejectPending(reason)
      logger.warn('Python backend exited unexpectedly', reason)
    })
  }

  private consume(child: ChildProcessWithoutNullStreams, chunk: string): void {
    if (this.process !== child) return
    this.buffer += chunk
    const lines = this.buffer.split('\n')
    this.buffer = lines.pop() ?? ''
    for (const line of lines) {
      let response: Record<string, unknown>
      try {
        const parsed: unknown = JSON.parse(line)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Response is not an object')
        response = parsed as Record<string, unknown>
        if ((typeof response.id !== 'string' && typeof response.id !== 'number') || typeof response.ok !== 'boolean') {
          throw new Error('Response has an invalid shape')
        }
      } catch (error) {
        logger.warn('Malformed Python backend response', error)
        this.failProcess(child, new Error('Malformed JSON Lines response'), 'PhotoFlow backend returned an invalid response.')
        return
      }

      const id = String(response.id)
      const pending = this.pending.get(id)
      if (!pending) {
        logger.warn('Python backend returned an unknown response identifier')
        continue
      }
      clearTimeout(pending.timeout)
      this.pending.delete(id)
      if (response.ok === false) {
        const error = new Error(safeErrorMessage(response.error ?? 'Backend request failed'))
        logger.warn('Python backend command failed', response.error)
        pending.reject(error)
      } else {
        pending.resolve(response)
      }
    }
  }

  private failProcess(
    child: ChildProcessWithoutNullStreams,
    error: unknown,
    publicMessage = 'Python backend communication failed. Retry the operation.'
  ): void {
    if (this.process !== child) return
    this.process = undefined
    this.buffer = ''
    this.rejectPending(new Error(publicMessage))
    logger.error('Python backend communication failed', error)
    if (!child.killed) child.kill()
  }

  private rejectPending(error: Error): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timeout)
      this.pending.delete(id)
      pending.reject(error)
    }
  }

  private clearStartupChild(child: ChildProcessWithoutNullStreams): void {
    if (this.startupChild === child) this.startupChild = undefined
  }

  private async stopProcess(): Promise<void> {
    const startup = this.startupPromise
    if (startup) await startup.catch(() => undefined)
    const child = this.process
    if (!child) return

    this.process = undefined
    this.buffer = ''
    try {
      child.stdin.end()
    } catch (error) {
      logger.warn('Unable to close Python backend input cleanly', error)
    }

    if (await this.waitForExit(child, 2_000)) {
      logger.info('Python backend stopped')
      return
    }
    logger.warn('Python backend did not stop in time; terminating it')
    if (!child.killed) child.kill()
    if (!(await this.waitForExit(child, 1_000))) {
      throw new Error('Python backend did not exit after termination')
    }
    logger.info('Python backend stopped')
  }

  private waitForExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
    if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true)
    return new Promise((resolve) => {
      const finish = (exited: boolean): void => {
        clearTimeout(timeout)
        child.removeListener('exit', onExit)
        child.removeListener('error', onError)
        resolve(exited)
      }
      const onExit = (): void => finish(true)
      const onError = (): void => finish(false)
      const timeout = setTimeout(() => finish(false), timeoutMs)
      child.once('exit', onExit)
      child.once('error', onError)
    })
  }
}

