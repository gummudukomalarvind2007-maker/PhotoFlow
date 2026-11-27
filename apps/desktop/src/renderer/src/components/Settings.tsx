import { useCallback, useEffect, useState } from 'react'
import { Icon } from './Icon'
import { thumbnailCache } from './thumbnailCache'

interface SettingsProps {
  onTransferStarted: (job: TransferJob) => void
  transferBusy: boolean
}

export function Settings({ onTransferStarted, transferBusy }: SettingsProps) {
  const [settings, setSettings] = useState<ApplicationSettings>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const [message, setMessage] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [savingPreference, setSavingPreference] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(undefined)
    try {
      setSettings(await window.photoFlow.getApplicationSettings())
    } catch (loadError) {
      setError(errorMessage(loadError, 'Could not load application settings.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const clearCache = async (): Promise<void> => {
    setBusy(true)
    setError(undefined)
    setMessage(undefined)
    try {
      const result = await window.photoFlow.clearThumbnailCache()
      if (!result.cancelled && result.job) {
        thumbnailCache.clear()
        onTransferStarted(result.job)
        setMessage('Thumbnail cache cleanup started.')
      }
    } catch (clearError) {
      setError(errorMessage(clearError, 'Could not clear the thumbnail cache.'))
    } finally {
      setBusy(false)
    }
  }

  const setSkipUnchanged = async (value: boolean): Promise<void> => {
    if (!settings) return
    const previous = settings.skip_unchanged_files
    setSettings({ ...settings, skip_unchanged_files: value })
    setSavingPreference(true)
    setError(undefined)
    try {
      const saved = await window.photoFlow.setSkipUnchangedFiles(value)
      setSettings((current) => current ? { ...current, skip_unchanged_files: saved } : current)
    } catch (saveError) {
      setSettings((current) => current ? { ...current, skip_unchanged_files: previous } : current)
      setError(errorMessage(saveError, 'Could not save this scan preference.'))
    } finally {
      setSavingPreference(false)
    }
  }

  return <section className="settings-page">
    <header className="operation-page-header"><div><p className="section-kicker">LOCAL APPLICATION</p><h1>Settings</h1><p>PhotoFlow stores its database and generated previews separately from original media.</p></div><button className="quiet-button" onClick={() => void refresh()} disabled={loading}><Icon name="rotate" size={15} /> Refresh</button></header>
    {error && <p className="app-error operation-error" role="alert">{error}</p>}
    {message && <p className="album-success" role="status">{message}</p>}
    {loading && !settings
      ? <div className="collection-empty" role="status">Loading application settings…</div>
      : settings && <>
        <section className="settings-section">
          <div className="settings-section-heading"><p className="section-kicker">LIBRARY STATUS</p><span className="settings-connected"><i className="status-dot online" />Backend connected</span></div>
          <div className="settings-stat-grid">
            <SettingStat label="Photos" value={settings.photo_count.toLocaleString()} icon="image" />
            <SettingStat label="Videos" value={settings.video_count.toLocaleString()} icon="video" />
            <SettingStat label="Indexed media size" value={formatBytes(settings.library_size_bytes)} icon="database" />
            <SettingStat label="Application version" value={`v${settings.application_version}`} icon="grid" />
          </div>
        </section>

        <section className="settings-section">
          <div className="settings-section-heading"><p className="section-kicker">STORAGE LOCATIONS</p></div>
          <SettingPath label="Database location" value={settings.database_path} />
          <SettingPath label="Thumbnail cache location" value={settings.thumbnail_cache_path} />
          <SettingPath label="Application data folder" value={settings.application_data_path} />
        </section>

        <section className="settings-section">
          <div className="settings-section-heading"><p className="section-kicker">LOCAL TOOLS</p></div>
          <div className="settings-tool-list">
            <ToolStatus name="Python backend" value={settings.python_version} available />
            <ToolStatus name="FFmpeg" value={settings.ffmpeg_available ? 'Available' : 'Not found on PATH'} available={settings.ffmpeg_available} />
            <ToolStatus name="FFprobe" value={settings.ffprobe_available ? 'Available' : 'Not found on PATH'} available={settings.ffprobe_available} />
          </div>
        </section>

        <section className="settings-section">
          <div className="settings-section-heading"><div><p className="section-kicker">APPEARANCE &amp; SCANNING</p></div></div>
          <div className="settings-preference"><span><strong>Theme</strong><small>Current interface appearance</small></span><b>Dark {settings.theme_customization_supported ? '' : '· fixed'}</b></div>
          <label className="settings-preference"><span><strong>Skip unchanged files</strong><small>When disabled, unchanged files are re-indexed during future scans.</small></span><input type="checkbox" checked={settings.skip_unchanged_files} disabled={savingPreference} onChange={(event) => void setSkipUnchanged(event.target.checked)} /></label>
        </section>

        <section className="settings-section cache-management">
          <div><p className="section-kicker">THUMBNAIL CACHE</p><h2>Manage generated previews</h2><p>{settings.thumbnail_cache_files.toLocaleString()} cached files · {formatBytes(settings.thumbnail_cache_bytes)}</p><small>Clearing the cache does not affect original media. Previews may be generated again when needed.</small></div>
          <button className="quiet-button danger-button" disabled={busy || transferBusy || settings.thumbnail_cache_files === 0} onClick={() => void clearCache()}>Clear cache</button>
        </section>
      </>}
  </section>
}

function SettingStat({ label, value, icon }: { label: string; value: string; icon: string }) {
  return <div className="settings-stat"><span><Icon name={icon} size={16} /></span><small>{label}</small><strong>{value}</strong></div>
}

function SettingPath({ label, value }: { label: string; value: string }) {
  return <div className="settings-path"><span>{label}</span><code title={value}>{value}</code></div>
}

function ToolStatus({ name, value, available }: { name: string; value: string; available: boolean }) {
  return <div className="settings-tool"><span><i className={`status-dot ${available ? 'online' : ''}`} />{name}</span><small>{value}</small></div>
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes < 1_000_000) return `${Math.round(bytes / 1_000)} KB`
  if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`
  return `${(bytes / 1_000_000_000).toFixed(2)} GB`
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
