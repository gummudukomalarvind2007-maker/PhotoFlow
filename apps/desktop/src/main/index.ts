import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { pathToFileURL } from 'node:url'
import { existsSync, realpathSync, statSync } from 'node:fs'
import { basename, delimiter, dirname, extname, isAbsolute, join, resolve } from 'node:path'
import { logger } from './logger'
import { focusApplicationWindow, initializeSingleInstance } from './instanceSafety'
import {
  PythonBackend,
  type OccasionGrouping,
  type MediaFilters,
  type MediaRemovalSelection,
  type PhotoReviewCategory,
  type PhotoReviewMark,
  type PhotoReviewStatus,
  type SmartAlbumKind,
  type SmartCollectionKind
} from './pythonBackend'

const backend = new PythonBackend()
let mainWindow: BrowserWindow | undefined
let shutdownStarted = false
let shutdownComplete = false
const hasSingleInstanceLock = initializeSingleInstance(app, () => focusApplicationWindow(mainWindow))
const exportExtensions = new Set([
  '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.heic', '.heif',
  '.mp4', '.mov', '.avi', '.mkv', '.webm'
])

function selectedDirectory(path: string): string {
  if (!isAbsolute(path)) throw new Error('Invalid selected folder')
  try {
    const resolved = realpathSync(path)
    if (!statSync(resolved).isDirectory()) throw new Error('Selected destination must be a folder')
    return resolved
  } catch {
    throw new Error('Selected destination is unavailable')
  }
}

function selectedSaveFile(path: string, suffixes: string[]): string {
  if (!isAbsolute(path) || !basename(path)) throw new Error('Invalid destination file')
  let parent: string
  try {
    parent = realpathSync(dirname(resolve(path)))
    if (!statSync(parent).isDirectory()) throw new Error('Destination folder is unavailable')
  } catch {
    throw new Error('Destination folder is unavailable')
  }
  const target = join(parent, basename(path))
  if (suffixes.length && !suffixes.includes(extname(target).toLowerCase())) throw new Error('Unsupported destination file type')
  if (existsSync(target)) throw new Error('That file already exists. Choose a different filename.')
  return target
}

function selectedOpenFile(path: string, suffixes: string[]): string {
  if (!isAbsolute(path)) throw new Error('Invalid selected file')
  let resolved: string
  try {
    resolved = realpathSync(path)
    if (!statSync(resolved).isFile()) throw new Error('Selected item must be a file')
  } catch {
    throw new Error('Selected backup file is unavailable')
  }
  if (suffixes.length && !suffixes.includes(extname(resolved).toLowerCase())) throw new Error('Unsupported backup file type')
  return resolved
}

function validatedExportFilters(value: unknown): MediaFilters {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid media filters')
  const filters = value as Record<string, unknown>
  const allowed = new Set([
    'search', 'extension', 'date_from', 'date_to', 'camera_make', 'camera_model',
    'min_width', 'max_width', 'min_height', 'max_height',
    'min_resolution_px', 'max_resolution_px',
    'min_duration_seconds', 'max_duration_seconds',
    'min_size_bytes', 'max_size_bytes', 'sort'
  ])
  if (Object.keys(filters).some((key) => !allowed.has(key))) throw new Error('Invalid media filters')
  for (const [key, maximum] of [['search', 200], ['extension', 16], ['date_from', 10], ['date_to', 10], ['camera_make', 100], ['camera_model', 100]] as const) {
    const filter = filters[key]
    if (filter !== undefined && typeof filter !== 'string') throw new Error(`Invalid ${key} filter`)
    if (typeof filter === 'string' && filter.length > maximum) throw new Error(`${key} filter is too long`)
  }
  if (typeof filters.extension === 'string' && filters.extension && !exportExtensions.has(filters.extension.toLowerCase())) {
    throw new Error('Unsupported media extension')
  }
  if (filters.sort !== undefined && !['newest', 'oldest', 'filename-asc', 'filename-desc', 'size-desc', 'size-asc'].includes(String(filters.sort))) {
    throw new Error('Invalid media sort order')
  }
  const integerFilters = {
    min_width: [1, 100_000], max_width: [1, 100_000],
    min_height: [1, 100_000], max_height: [1, 100_000],
    min_resolution_px: [1, 10_000_000_000], max_resolution_px: [1, 10_000_000_000],
    min_size_bytes: [0, Number.MAX_SAFE_INTEGER], max_size_bytes: [0, Number.MAX_SAFE_INTEGER]
  } as const
  for (const [key, [minimum, maximum]] of Object.entries(integerFilters)) {
    const filter = filters[key]
    if (filter !== undefined && (
      typeof filter !== 'number' || !Number.isSafeInteger(filter) || filter < minimum || filter > maximum
    )) throw new Error(`Invalid ${key} filter`)
  }
  for (const key of ['min_duration_seconds', 'max_duration_seconds']) {
    const filter = filters[key]
    if (filter !== undefined && (
      typeof filter !== 'number' || !Number.isFinite(filter) || filter < 0 || filter > 10_000_000
    )) throw new Error(`Invalid ${key} filter`)
  }
  for (const [minimumKey, maximumKey] of [
    ['min_width', 'max_width'],
    ['min_height', 'max_height'],
    ['min_resolution_px', 'max_resolution_px'],
    ['min_duration_seconds', 'max_duration_seconds'],
    ['min_size_bytes', 'max_size_bytes']
  ]) {
    const minimum = filters[minimumKey]
    const maximum = filters[maximumKey]
    if (typeof minimum === 'number' && typeof maximum === 'number' && minimum > maximum) {
      throw new Error(`${minimumKey} must not be greater than ${maximumKey}`)
    }
  }
  return filters as MediaFilters
}

async function chooseExportDirectory(title: string): Promise<string | undefined> {
  const result = await dialog.showOpenDialog(mainWindow!, {
    title,
    buttonLabel: 'Choose destination',
    properties: ['openDirectory', 'createDirectory']
  })
  return result.canceled || result.filePaths.length === 0 ? undefined : selectedDirectory(result.filePaths[0])
}

function assertPositiveId(value: unknown, label: string): asserts value is number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) throw new Error(`Invalid ${label} identifier`)
}

function assertJobId(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) {
    throw new Error(`Invalid ${label} identifier`)
  }
}

function assertPage(limit: unknown, offset: unknown): asserts limit is number {
  if (typeof limit !== 'number' || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    throw new Error('Invalid page size')
  }
  if (typeof offset !== 'number' || !Number.isSafeInteger(offset) || offset < 0 || offset > Number.MAX_SAFE_INTEGER) {
    throw new Error('Invalid page offset')
  }
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 900,
    minHeight: 600,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      devTools: !app.isPackaged
    }
  })

  mainWindow.once('ready-to-show', () => mainWindow?.show())
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\/[^/]/i.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  mainWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  mainWindow.webContents.on('will-attach-webview', (event) => event.preventDefault())
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const devUrl = process.env.ELECTRON_RENDERER_URL
    if (!app.isPackaged && devUrl) {
      try {
        const expected = new URL(devUrl)
        const requested = new URL(url)
        if (requested.origin === expected.origin) return
      } catch {
        event.preventDefault()
        return
      }
    }
    const rendererUrl = pathToFileURL(join(__dirname, '../renderer/index.html')).toString()
    if (url === rendererUrl) return
    event.preventDefault()
  })

  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL) {
    void mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  if (!hasSingleInstanceLock) return
  logger.info('PhotoFlow starting')
  ipcMain.handle('app:version', () => app.getVersion())
  ipcMain.handle('backend:health', () => backend.health())
  ipcMain.handle('library:select-and-scan', async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: 'Select media folders',
      buttonLabel: 'Scan selected folders',
      properties: ['openDirectory', 'multiSelections']
    })
    if (result.canceled || result.filePaths.length === 0) return { cancelled: true }
    return { cancelled: false, scan: await backend.startScan(result.filePaths) }
  })
  ipcMain.handle('library:scan-status', (_event, scanId: unknown) => {
    assertJobId(scanId, 'scan')
    return backend.scanStatus(scanId)
  })
  ipcMain.handle('library:cancel-scan', (_event, scanId: unknown) => {
    assertJobId(scanId, 'scan')
    return backend.cancelScan(scanId)
  })
  ipcMain.handle('library:resume-scan', (_event, scanId: unknown) => {
    assertJobId(scanId, 'scan')
    return backend.resumeScan(scanId)
  })
  ipcMain.handle('library:retry-failed-scan', (_event, scanId: unknown) => {
    assertJobId(scanId, 'scan')
    return backend.retryFailedScan(scanId)
  })
  ipcMain.handle('library:scan-history', () => backend.scanHistory())
  ipcMain.handle('library:failed-items', () => backend.failedItems())
  ipcMain.handle('library:resumable-scan', () => backend.resumableScan())
  ipcMain.handle('library:overview', () => backend.libraryOverview())
  ipcMain.handle('library:list-media', (_event, mediaType: unknown, limit: unknown = 60, offset: unknown = 0, filters: unknown = {}) => {
    if (mediaType !== 'all' && mediaType !== 'photo' && mediaType !== 'video') throw new Error('Invalid media type')
    if (typeof limit !== 'number' || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
      throw new Error('Invalid media page size')
    }
    if (typeof offset !== 'number' || !Number.isSafeInteger(offset) || offset < 0) {
      throw new Error('Invalid media page offset')
    }
    return backend.listMedia(mediaType, limit, offset, validatedExportFilters(filters))
  })
  ipcMain.handle('library:count-media', (_event, mediaType: unknown, filters: unknown = {}) => {
    if (mediaType !== 'all' && mediaType !== 'photo' && mediaType !== 'video') throw new Error('Invalid media type')
    return backend.countMedia(mediaType, validatedExportFilters(filters))
  })
  ipcMain.handle('library:remove-media', (_event, value: unknown) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid media selection')
    const selection = value as Record<string, unknown>
    if (selection.mode === 'ids') {
      const ids = selection.media_ids
      if (!Array.isArray(ids) || ids.length < 1 || ids.length > 5000) throw new Error('Select between 1 and 5000 media items')
      ids.forEach((id) => assertPositiveId(id, 'media'))
      return backend.removeMedia({ mode: 'ids', media_ids: ids as number[] })
    }
    if (selection.mode === 'filtered') {
      const mediaType = selection.media_type
      if (mediaType !== 'all' && mediaType !== 'photo' && mediaType !== 'video') throw new Error('Invalid media type')
      const excludedIds = selection.excluded_ids
      if (!Array.isArray(excludedIds) || excludedIds.length > 100_000) throw new Error('Invalid excluded media selection')
      excludedIds.forEach((id) => assertPositiveId(id, 'media'))
      const filters = validatedExportFilters(selection.filters)
      const validated: MediaRemovalSelection = {
        mode: 'filtered',
        media_type: mediaType,
        filters,
        excluded_ids: excludedIds as number[]
      }
      return backend.removeMedia(validated)
    }
    throw new Error('Invalid media selection')
  })
  ipcMain.handle('library:thumbnail', (_event, mediaId: unknown) => {
    if (!Number.isSafeInteger(mediaId) || (mediaId as number) < 1) throw new Error('Invalid media identifier')
    return backend.thumbnail(mediaId as number)
  })
  ipcMain.handle('library:full-resolution', (_event, mediaId: unknown) => {
    if (!Number.isSafeInteger(mediaId) || (mediaId as number) < 1) throw new Error('Invalid media identifier')
    return backend.fullResolution(mediaId as number)
  })
  ipcMain.handle('albums:list', () => backend.listAlbums())
  ipcMain.handle('albums:create', (_event, name: unknown, description: unknown = '') => {
    if (typeof name !== 'string' || name.trim().length < 1 || name.trim().length > 100) throw new Error('Invalid album name')
    if (typeof description !== 'string' || description.length > 500) throw new Error('Invalid album description')
    return backend.createAlbum(name, description)
  })
  ipcMain.handle('albums:rename', (_event, albumId: unknown, name: unknown, description: unknown = '') => {
    assertPositiveId(albumId, 'album')
    if (typeof name !== 'string' || name.trim().length < 1 || name.trim().length > 100) throw new Error('Invalid album name')
    if (typeof description !== 'string' || description.length > 500) throw new Error('Invalid album description')
    return backend.renameAlbum(albumId, name, description)
  })
  ipcMain.handle('albums:delete', (_event, albumId: unknown) => {
    assertPositiveId(albumId, 'album')
    return backend.deleteAlbum(albumId)
  })
  ipcMain.handle('albums:add-media', (_event, albumId: unknown, mediaIds: unknown) => {
    assertPositiveId(albumId, 'album')
    if (!Array.isArray(mediaIds) || mediaIds.length < 1 || mediaIds.length > 500 ||
        !mediaIds.every((mediaId: unknown): mediaId is number =>
          typeof mediaId === 'number' && Number.isSafeInteger(mediaId) && mediaId > 0)) {
      throw new Error('Invalid media selection')
    }
    return backend.addAlbumMedia(albumId, mediaIds)
  })
  ipcMain.handle('albums:remove-media', (_event, albumId: unknown, mediaIds: unknown) => {
    assertPositiveId(albumId, 'album')
    if (!Array.isArray(mediaIds) || mediaIds.length < 1 || mediaIds.length > 500 ||
        !mediaIds.every((mediaId: unknown): mediaId is number =>
          typeof mediaId === 'number' && Number.isSafeInteger(mediaId) && mediaId > 0)) {
      throw new Error('Invalid media selection')
    }
    return backend.removeAlbumMedia(albumId, mediaIds)
  })
  ipcMain.handle('albums:media', (_event, albumId: unknown, limit: unknown = 60, offset: unknown = 0) => {
    assertPositiveId(albumId, 'album')
    assertPage(limit, offset)
    return backend.listAlbumMedia(albumId, limit, offset as number)
  })
  ipcMain.handle('smart-albums:list', () => backend.listSmartAlbums())
  ipcMain.handle('smart-albums:media', (_event, kind: unknown, value: unknown, limit: unknown = 60, offset: unknown = 0) => {
    const kinds: SmartAlbumKind[] = ['year', 'month', 'camera', 'media_type', 'recent', 'videos']
    if (typeof kind !== 'string' || !kinds.includes(kind as SmartAlbumKind)) throw new Error('Invalid smart album')
    if (typeof value !== 'string' || value.length > 250) throw new Error('Invalid smart album')
    assertPage(limit, offset)
    return backend.listSmartAlbumMedia(kind as SmartAlbumKind, value, limit, offset as number)
  })
  ipcMain.handle('smart-collections:list', () => backend.listSmartCollections())
  ipcMain.handle('smart-collections:media', (_event, kind: unknown, value: unknown, limit: unknown = 60, offset: unknown = 0) => {
    const kinds: SmartCollectionKind[] = [
      'recently_added', 'recently_viewed', 'screenshots', 'portrait', 'landscape',
      'high_resolution', 'large_files', 'photo_year', 'photo_month', 'similar_dimensions', 'video_duration'
    ]
    if (typeof kind !== 'string' || !kinds.includes(kind as SmartCollectionKind)) throw new Error('Invalid smart collection')
    if (typeof value !== 'string' || value.length > 250) throw new Error('Invalid smart collection')
    assertPage(limit, offset)
    return backend.listSmartCollectionMedia(kind as SmartCollectionKind, value, limit, offset as number)
  })
  ipcMain.handle('saved-smart-albums:list', () => backend.listSavedSmartAlbums())
  ipcMain.handle('saved-smart-albums:create', (_event, name: unknown, description: unknown, mediaType: unknown, filters: unknown) => {
    if (typeof name !== 'string' || name.trim().length < 1 || name.trim().length > 100) throw new Error('Invalid smart album name')
    if (typeof description !== 'string' || description.length > 500) throw new Error('Invalid smart album description')
    if (mediaType !== 'all' && mediaType !== 'photo' && mediaType !== 'video') throw new Error('Invalid smart album media type')
    return backend.createSavedSmartAlbum(name, description, mediaType, validatedExportFilters(filters))
  })
  ipcMain.handle('saved-smart-albums:delete', (_event, albumId: unknown) => {
    assertPositiveId(albumId, 'smart album')
    return backend.deleteSavedSmartAlbum(albumId)
  })
  ipcMain.handle('saved-smart-albums:media', (_event, albumId: unknown, limit: unknown = 60, offset: unknown = 0) => {
    assertPositiveId(albumId, 'smart album')
    assertPage(limit, offset)
    return backend.listSavedSmartAlbumMedia(albumId, limit, offset as number)
  })
  ipcMain.handle('occasions:list', (_event, groupBy: unknown, dateFrom: unknown, dateTo: unknown, limit: unknown = 100, offset: unknown = 0) => {
    const groupings: OccasionGrouping[] = ['year', 'month', 'day']
    if (typeof groupBy !== 'string' || !groupings.includes(groupBy as OccasionGrouping)) throw new Error('Invalid occasion grouping')
    for (const date of [dateFrom, dateTo]) {
      if (date !== undefined && date !== null && date !== '' && (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date))) {
        throw new Error('Invalid occasion date')
      }
    }
    assertPage(limit, offset)
    return backend.listOccasions(
      groupBy as OccasionGrouping,
      typeof dateFrom === 'string' && dateFrom ? dateFrom : undefined,
      typeof dateTo === 'string' && dateTo ? dateTo : undefined,
      limit,
      offset as number
    )
  })
  ipcMain.handle('occasions:media', (_event, groupBy: unknown, period: unknown, dateSource: unknown, limit: unknown = 60, offset: unknown = 0) => {
    const groupings: OccasionGrouping[] = ['year', 'month', 'day']
    if (typeof groupBy !== 'string' || !groupings.includes(groupBy as OccasionGrouping)) throw new Error('Invalid occasion grouping')
    if (typeof period !== 'string' || period.length > 10) throw new Error('Invalid occasion')
    if (dateSource !== 'captured' && dateSource !== 'indexed') throw new Error('Invalid occasion date source')
    assertPage(limit, offset)
    return backend.listOccasionMedia(groupBy as OccasionGrouping, period, dateSource, limit, offset as number)
  })
  ipcMain.handle('photo-review:start', (_event, threshold: unknown) => {
    if (typeof threshold !== 'number' || !Number.isSafeInteger(threshold) || threshold < 0 || threshold > 7) {
      throw new Error('Invalid similarity threshold')
    }
    return backend.startPhotoReview(threshold)
  })
  ipcMain.handle('photo-review:retry-failed', () => backend.retryFailedPhotoAnalysis())
  ipcMain.handle('photo-review:status', (_event, jobId: unknown) => {
    if (jobId !== undefined && jobId !== null) assertJobId(jobId, 'analysis job')
    return backend.photoReviewStatus(jobId as string | undefined)
  })
  ipcMain.handle('photo-review:cancel', (_event, jobId: unknown) => {
    assertJobId(jobId, 'analysis job')
    return backend.cancelPhotoReview(jobId)
  })
  ipcMain.handle('photo-review:overview', (_event, threshold: unknown) => {
    if (typeof threshold !== 'number' || !Number.isSafeInteger(threshold) || threshold < 0 || threshold > 7) {
      throw new Error('Invalid similarity threshold')
    }
    return backend.photoReviewOverview(threshold)
  })
  ipcMain.handle('photo-review:list', (_event, category: unknown, status: unknown, limit: unknown, offset: unknown, threshold: unknown) => {
    const categories = ['exact', 'similar', 'blurry', 'low_resolution', 'exposure']
    if (typeof category !== 'string' || !categories.includes(category)) throw new Error('Invalid review category')
    const statuses = ['pending', 'later', 'reviewed', 'all']
    if (typeof status !== 'string' || !statuses.includes(status)) throw new Error('Invalid review status')
    if (typeof limit !== 'number' || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new Error('Invalid review page size')
    if (typeof offset !== 'number' || !Number.isSafeInteger(offset) || offset < 0) throw new Error('Invalid review page offset')
    if (typeof threshold !== 'number' || !Number.isSafeInteger(threshold) || threshold < 0 || threshold > 7) {
      throw new Error('Invalid similarity threshold')
    }
    return backend.listPhotoReview(category as PhotoReviewCategory, status as PhotoReviewStatus, limit, offset, threshold)
  })
  ipcMain.handle('photo-review:set-status', (_event, mediaId: unknown, status: unknown) => {
    if (!Number.isSafeInteger(mediaId) || (mediaId as number) < 1) throw new Error('Invalid media identifier')
    if (status !== 'pending' && status !== 'later' && status !== 'reviewed') throw new Error('Invalid review status')
    return backend.setPhotoReviewStatus(mediaId as number, status as PhotoReviewMark)
  })
  ipcMain.handle('export:media-selected', async (_event, mediaIds: unknown, organizeByType: unknown) => {
    if (!Array.isArray(mediaIds) || mediaIds.length < 1 || mediaIds.length > 5000 ||
        !mediaIds.every((mediaId: unknown): mediaId is number =>
          typeof mediaId === 'number' && Number.isSafeInteger(mediaId) && mediaId > 0)) {
      throw new Error('Select between 1 and 5000 media items')
    }
    if (typeof organizeByType !== 'boolean') throw new Error('Invalid export organization option')
    const destination = await chooseExportDirectory('Export selected media')
    if (!destination) return { cancelled: true }
    return { cancelled: false, job: await backend.exportSelectedMedia(destination, mediaIds, organizeByType) }
  })
  ipcMain.handle('export:album', async (_event, albumId: unknown, organizeByType: unknown) => {
    assertPositiveId(albumId, 'album')
    if (typeof organizeByType !== 'boolean') throw new Error('Invalid export organization option')
    const destination = await chooseExportDirectory('Export album media')
    if (!destination) return { cancelled: true }
    return { cancelled: false, job: await backend.exportAlbum(destination, albumId, organizeByType) }
  })
  ipcMain.handle('export:filtered', async (_event, mediaType: unknown, filters: unknown, organizeByType: unknown) => {
    if (mediaType !== 'all' && mediaType !== 'photo' && mediaType !== 'video') throw new Error('Invalid media type')
    if (typeof organizeByType !== 'boolean') throw new Error('Invalid export organization option')
    const validatedFilters = validatedExportFilters(filters)
    const destination = await chooseExportDirectory('Export filtered media')
    if (!destination) return { cancelled: true }
    return { cancelled: false, job: await backend.exportFilteredMedia(destination, mediaType, validatedFilters, organizeByType) }
  })
  ipcMain.handle('export:metadata', async (_event, format: unknown) => {
    if (format !== 'csv' && format !== 'json') throw new Error('Invalid metadata export format')
    const result = await dialog.showSaveDialog(mainWindow!, {
      title: `Export library metadata as ${format.toUpperCase()}`,
      defaultPath: `photoflow-library.${format}`,
      filters: [{ name: format.toUpperCase(), extensions: [format] }]
    })
    if (result.canceled || !result.filePath) return { cancelled: true }
    const destination = selectedSaveFile(result.filePath, [`.${format}`])
    return { cancelled: false, job: await backend.exportMetadata(destination, format) }
  })
  ipcMain.handle('backup:create', async () => {
    const result = await dialog.showSaveDialog(mainWindow!, {
      title: 'Create PhotoFlow database backup',
      defaultPath: 'photoflow-backup.db',
      filters: [{ name: 'SQLite database', extensions: ['db', 'sqlite', 'sqlite3'] }]
    })
    if (result.canceled || !result.filePath) return { cancelled: true }
    const destination = selectedSaveFile(result.filePath, ['.db', '.sqlite', '.sqlite3'])
    return { cancelled: false, job: await backend.createDatabaseBackup(destination) }
  })
  ipcMain.handle('backup:restore', async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: 'Select a PhotoFlow database backup',
      buttonLabel: 'Select backup',
      properties: ['openFile'],
      filters: [{ name: 'SQLite database', extensions: ['db', 'sqlite', 'sqlite3'] }]
    })
    if (result.canceled || result.filePaths.length === 0) return { cancelled: true }
    const source = selectedOpenFile(result.filePaths[0], ['.db', '.sqlite', '.sqlite3'])
    const confirmation = await dialog.showMessageBox(mainWindow!, {
      type: 'warning',
      title: 'Restore PhotoFlow library?',
      message: 'Restoring replaces the current PhotoFlow database with the selected backup.',
      detail: 'A safety backup of the current database will be created first. Your original photos and videos will not be changed.',
      buttons: ['Restore library', 'Cancel'],
      defaultId: 1,
      cancelId: 1,
      noLink: true
    })
    if (confirmation.response !== 0) return { cancelled: true }
    return { cancelled: false, job: await backend.restoreDatabaseBackup(source) }
  })
  ipcMain.handle('settings:overview', async () => ({
    ...await backend.settingsOverview(),
    application_version: app.getVersion()
  }))
  ipcMain.handle('settings:scan-preference', (_event, skipUnchangedFiles: unknown) => {
    if (typeof skipUnchangedFiles !== 'boolean') throw new Error('Invalid scan preference')
    return backend.setSkipUnchangedFiles(skipUnchangedFiles)
  })
  ipcMain.handle('settings:clear-cache', async () => {
    const confirmation = await dialog.showMessageBox(mainWindow!, {
      type: 'warning',
      title: 'Clear thumbnail cache?',
      message: 'This removes generated thumbnails from PhotoFlow’s cache.',
      detail: 'Original photos and videos are never deleted. Thumbnails can be generated again when needed.',
      buttons: ['Clear cache', 'Cancel'],
      defaultId: 1,
      cancelId: 1,
      noLink: true
    })
    if (confirmation.response !== 0) return { cancelled: true }
    return { cancelled: false, job: await backend.startCacheClear() }
  })
  ipcMain.handle('transfer:status', (_event, jobId: unknown) => {
    assertJobId(jobId, 'transfer')
    return backend.transferStatus(jobId)
  })
  ipcMain.handle('transfer:cancel', (_event, jobId: unknown) => {
    assertJobId(jobId, 'transfer')
    return backend.cancelTransfer(jobId)
  })
  createWindow()
  void backend.health()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (!hasSingleInstanceLock) return
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', (event) => {
  if (!hasSingleInstanceLock) return
  if (shutdownComplete) return
  event.preventDefault()
  if (shutdownStarted) return

  shutdownStarted = true
  logger.info('PhotoFlow shutting down')
  void backend.stop().catch((error: unknown) => {
    logger.error('Error stopping Python backend', error)
  }).finally(() => {
    shutdownComplete = true
    app.quit()
  })
})

