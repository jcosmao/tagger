import './style.css'
import { api, Track, Artist, Album, GenreOps, LookupResult, AppSettings, AlbumInconsistency, UnifyField, ScanJob, ArtistDetail, setUnauthorizedHandler } from './api'
import { toast } from './toast'
import { esc, fmtDuration, debounce } from './util'
import { state, PAGE_SIZE, TAG_FIELDS, DirNode, SidebarMode, saveColPrefs } from './state'
import { COL_DEFS } from './columns'
import {
  trackQuality, QUALITY_TITLES, QUALITY_ISSUES,
  toTitleCase, needsNormalization, NORMALIZE_FIELDS,
} from './quality'
import { APP_HTML } from './template'

// ─── Layout ───────────────────────────────────────────────────────────────────

document.querySelector<HTMLDivElement>('#app')!.innerHTML = APP_HTML

// ─── Element refs ─────────────────────────────────────────────────────────────

const appEl          = document.querySelector<HTMLDivElement>('#app')!
const artistListEl   = document.getElementById('artist-list')!
const genreListEl    = document.getElementById('genre-list')!
const artistKeyListEl = document.getElementById('artist-key-list')!
const artistFilterEl = document.getElementById('artist-filter') as HTMLInputElement
const artistGenresEl = document.getElementById('artist-genres')!
const fetchAllBtn    = document.getElementById('fetch-all-btn') as HTMLButtonElement
const fetchAllStatus = document.getElementById('fetch-all-status')!
const genreFilterEl  = document.getElementById('genre-filter') as HTMLInputElement
const genreOptionsEl = document.getElementById('genre-options')!
const genreModeEl    = document.getElementById('genre-mode') as HTMLSelectElement
const selectMatchingBtn = document.getElementById('select-matching-btn') as HTMLButtonElement
const dirTreeEl      = document.getElementById('dir-tree')!
const trackTheadRow  = document.getElementById('track-thead-row')!
const trackTbody     = document.getElementById('track-tbody')!
const trackEmpty     = document.getElementById('track-empty')!
const trackLoading   = document.getElementById('track-loading')!
const trackCount     = document.getElementById('track-count')!
const tagEditor      = document.getElementById('tag-editor')!
const tagForm        = document.getElementById('tag-form') as HTMLFormElement
const editorTitle    = document.getElementById('editor-title')!
const bulkActions    = document.getElementById('bulk-actions')!
const selectionCount = document.getElementById('selection-count')!
const searchEl       = document.getElementById('search') as HTMLInputElement
const scanBtn        = document.getElementById('scan-btn') as HTMLButtonElement
const rescanFolderBtn = document.getElementById('rescan-folder-btn') as HTMLButtonElement
const scanStatusEl   = document.getElementById('scan-status')!
const colPickerBtn   = document.getElementById('col-picker-btn')!
const colPickerEl    = document.getElementById('col-picker')!
const albumGridEl    = document.getElementById('album-grid')!
const tableWrapEl    = document.querySelector<HTMLElement>('.table-wrap')!
const viewListBtn    = document.getElementById('view-list-btn')!
const viewAlbumsBtn  = document.getElementById('view-albums-btn')!
const coverImg         = document.getElementById('cover-img') as HTMLImageElement
const coverPlaceholder = document.getElementById('cover-placeholder')!
const coverInput       = document.getElementById('cover-input') as HTMLInputElement
const playerEl         = document.getElementById('player') as HTMLAudioElement
const editorRenamePreviewEl = document.getElementById('editor-rename-preview')!
const spectroSection   = document.getElementById('spectro-section')!
const spectroBtn       = document.getElementById('spectro-btn') as HTMLButtonElement
const spectroWrap      = document.getElementById('spectro-wrap')!
const spectroImg       = document.getElementById('spectro-img') as HTMLImageElement
const spectroStatus    = document.getElementById('spectro-status')!
const spectroPopout    = document.getElementById('spectro-popout') as HTMLButtonElement
const sidebarEl        = document.querySelector<HTMLElement>('.sidebar')!
const editorResizer    = document.getElementById('editor-resizer')!
let spectrogramAvailable = false

// Rename settings mirrored client-side for the editor's live save-path preview.
let renameOnSave = false
let renameTemplate = ''
const lookupBtn        = document.getElementById('lookup-btn') as HTMLButtonElement
const lookupPanel      = document.getElementById('lookup-panel')!
const lookupResults    = document.getElementById('lookup-results')!
const qualityListEl    = document.getElementById('quality-list')!
const normalizeCaseBtn  = document.getElementById('normalize-case-btn') as HTMLButtonElement
const autonumberBtn     = document.getElementById('autonumber-btn') as HTMLButtonElement
const findReplaceBtn    = document.getElementById('find-replace-btn') as HTMLButtonElement
const removeTracksBtn   = document.getElementById('remove-tracks-btn') as HTMLButtonElement
const deleteFilesBtn    = document.getElementById('delete-files-btn') as HTMLButtonElement
const organizeBtn       = document.getElementById('organize-btn') as HTMLButtonElement
const replaygainBtn     = document.getElementById('replaygain-btn') as HTMLButtonElement
const filterQualityEl  = document.getElementById('filter-quality') as HTMLSelectElement
const filterFormatEl   = document.getElementById('filter-format') as HTMLSelectElement
const inferBtn         = document.getElementById('infer-btn') as HTMLButtonElement
const exportM3uBtn     = document.getElementById('export-m3u-btn') as HTMLButtonElement
const undoBtn          = document.getElementById('undo-btn') as HTMLButtonElement

// Whether the server has a ReplayGain tool available (set at init)
let replaygainAvailable = false

// ─── Helpers ──────────────────────────────────────────────────────────────────

function coverUrl(trackId: number): string {
  return `/api/covers/${trackId}${state.coverBust ? '?v=' + state.coverBust : ''}`
}

// In-page confirmation. Native confirm() can be silenced by the browser
// ("prevent this page from creating additional dialogs"), after which it
// returns false instantly and the action looks dead.
function confirmModal(title: string, message: string, confirmLabel: string): Promise<boolean> {
  return new Promise(resolve => {
    const overlay = document.createElement('div')
    overlay.className = 'modal-overlay'
    overlay.innerHTML = `
      <form class="modal-card">
        <div class="modal-title"></div>
        <div class="modal-hint modal-message"></div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" data-cancel>Cancel</button>
          <button type="submit" class="btn btn-danger"></button>
        </div>
      </form>
    `
    overlay.querySelector('.modal-title')!.textContent = title
    overlay.querySelector('.modal-message')!.textContent = message
    const ok = overlay.querySelector<HTMLButtonElement>('button[type=submit]')!
    ok.textContent = confirmLabel

    const done = (result: boolean) => {
      overlay.remove()
      document.removeEventListener('keydown', onKey, true)
      resolve(result)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); done(false) } }
    overlay.addEventListener('click', e => { if (e.target === overlay) done(false) })
    overlay.querySelector('[data-cancel]')!.addEventListener('click', () => done(false))
    overlay.querySelector('form')!.addEventListener('submit', e => { e.preventDefault(); done(true) })
    document.addEventListener('keydown', onKey, true)
    document.body.appendChild(overlay)
    ok.focus()
  })
}

function selectAll(): HTMLInputElement | null {
  return document.getElementById('select-all') as HTMLInputElement | null
}

// ─── Sidebar tabs ─────────────────────────────────────────────────────────────

function renderSidebarTabs() {
  document.querySelectorAll<HTMLButtonElement>('.stab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === state.sidebarMode)
  })
  document.getElementById('panel-tags')!.hidden    = state.sidebarMode !== 'tags'
  document.getElementById('panel-genres')!.hidden  = state.sidebarMode !== 'genres'
  document.getElementById('panel-artists')!.hidden = state.sidebarMode !== 'artists'
  renderArtistGenres()
  document.getElementById('panel-files')!.hidden   = state.sidebarMode !== 'files'
  document.getElementById('panel-quality')!.hidden = state.sidebarMode !== 'quality'
}

// ─── Tags panel ───────────────────────────────────────────────────────────────

function renderTagsPanel() {
  artistListEl.innerHTML = ''

  // All tracks
  const allLi = document.createElement('li')
  allLi.className = 'nav-item nav-all' + (!state.selectedArtist && state.selectedArtist !== '' ? ' active' : '')
  allLi.dataset.all = '1'
  allLi.innerHTML = `<span class="nav-icon">♪</span><span class="nav-label">All tracks</span>`
  artistListEl.appendChild(allLi)

  for (const a of state.artists) {
    const artistKey = a.artist
    const isSelected = state.selectedArtist === artistKey
    const isExpanded = state.expandedArtists.has(artistKey)
    const artistAlbums = state.albumsByArtist.get(artistKey) ?? []

    const artistLi = document.createElement('li')
    artistLi.className = 'nav-item nav-artist' + (isSelected && !state.selectedAlbum ? ' active' : '')
    artistLi.dataset.artist = artistKey
    artistLi.innerHTML = `
      <span class="nav-arrow" data-toggle="1">${isExpanded ? '▾' : '▸'}</span>
      <span class="nav-label">${esc(a.artist || '(Unknown Artist)')}</span>
      <span class="nav-count">${a.track_count}</span>
    `
    artistListEl.appendChild(artistLi)

    if (isExpanded) {
      for (const alb of artistAlbums) {
        const albLi = document.createElement('li')
        albLi.className = 'nav-item nav-album' + (isSelected && state.selectedAlbum === alb.album ? ' active' : '')
        albLi.dataset.artist = artistKey
        albLi.dataset.album  = alb.album
        albLi.innerHTML = `
          <span class="nav-label">${esc(alb.album || '(Unknown Album)')}</span>
          <span class="nav-count">${alb.track_count}</span>
        `
        artistListEl.appendChild(albLi)
      }
    }
  }
}

// ─── Artists panel ────────────────────────────────────────────────────────────

const ARTIST_BATCH = 300

function renderArtistsPanel() {
  artistKeyListEl.innerHTML = ''
  const allLi = document.createElement('li')
  allLi.className = 'nav-item nav-all' + (state.selectedArtistKey === null ? ' active' : '')
  allLi.dataset.all = '1'
  allLi.innerHTML = `<span class="nav-icon">♪</span><span class="nav-label">All tracks</span>`
  artistKeyListEl.appendChild(allLi)

  const needle = state.artistFilter.trim().toLowerCase()
  const matches = state.artistEntries.filter(a => !needle || a.artist.toLowerCase().includes(needle))
  // Thousands of artists: render in batches as the list scrolls.
  let rendered = 0
  const more = document.createElement('li')
  more.className = 'nav-more'
  const renderMore = () => {
    for (const a of matches.slice(rendered, rendered + ARTIST_BATCH)) {
      const li = document.createElement('li')
      li.className = 'nav-item nav-genre' + (state.selectedArtistKey === a.artist ? ' active' : '')
      li.dataset.artistKey = a.artist
      li.innerHTML = `
        <span class="nav-label">${esc(a.artist || '(Unknown artist)')}</span>
        ${a.fetched ? '<span class="nav-fetched" title="MusicBrainz genres fetched">●</span>' : ''}
        <span class="nav-count">${a.track_count}</span>
      `
      artistKeyListEl.insertBefore(li, more)
    }
    rendered += ARTIST_BATCH
    if (rendered >= matches.length) more.remove()
  }
  artistKeyListEl.appendChild(more)
  renderMore()
  if (rendered < matches.length) {
    const io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) renderMore()
      if (rendered >= matches.length) io.disconnect()
    }, { root: artistKeyListEl.parentElement, rootMargin: '400px' })
    io.observe(more)
  }
}

async function loadArtists() {
  try {
    state.artistEntries = await api.artists.list()
    renderArtistsPanel()
  } catch (e) {
    toast(`Failed to load artists: ${e}`, 'error')
  }
}

async function loadArtistDetail() {
  const key = state.selectedArtistKey
  state.artistDetail = null
  renderArtistGenres()
  if (key === null) return
  try {
    const detail = await api.artists.detail(key)
    if (state.selectedArtistKey === key) { state.artistDetail = detail; renderArtistGenres() }
  } catch (e) {
    toast(`Failed to load artist: ${e}`, 'error')
  }
}

function renderArtistGenres() {
  const d = state.artistDetail
  artistGenresEl.hidden = state.sidebarMode !== 'artists' || state.selectedArtistKey === null || !!state.query
  if (artistGenresEl.hidden) return
  if (!d) { artistGenresEl.innerHTML = '<div class="ag-muted">Loading…</div>'; return }

  const plural = (n: number) => `${n.toLocaleString()} track${n !== 1 ? 's' : ''}`
  const current = d.current_genres.length
    ? d.current_genres.map(g => `<span class="ag-chip">${esc(g.genre)} <b>×${g.track_count}</b></span>`).join('')
    : '<span class="ag-muted">none</span>'

  let mbRow: string
  const mb = d.mb
  if (!mb) {
    mbRow = `<span class="ag-muted">Not fetched yet.</span>`
  } else if (mb.error) {
    mbRow = `<span class="ag-error">MusicBrainz error: ${esc(mb.error)}</span>`
  } else if (!mb.mbid) {
    mbRow = `<span class="ag-muted">No matching artist on MusicBrainz.</span>`
  } else {
    const label = (c: { name: string | null; disambiguation: string | null }) =>
      esc(c.name ?? '') + (c.disambiguation ? ` (${esc(c.disambiguation)})` : '')
    const match = mb.candidates.length > 1
      ? `<select class="ag-candidates" title="Pick the right artist">${mb.candidates.map(c =>
          `<option value="${esc(c.id)}"${c.id === mb.mbid ? ' selected' : ''}>${label(c)}</option>`).join('')}</select>`
      : `<a href="https://musicbrainz.org/artist/${esc(mb.mbid)}" target="_blank" rel="noopener">${label({ name: mb.mb_name, disambiguation: mb.disambiguation })}</a>`
    const top = mb.genres[0]?.count ?? 0
    const chips = mb.genres.length
      ? mb.genres.map(g => `
          <label class="ag-chip ag-pick"><input type="checkbox" value="${esc(g.label)}"${g.count * 2 >= top ? ' checked' : ''} />
          ${esc(g.label)} <b>${g.count}</b></label>`).join('')
      : '<span class="ag-muted">This artist has no genres on MusicBrainz.</span>'
    mbRow = `${match}<div class="ag-chips">${chips}</div>`
  }

  artistGenresEl.innerHTML = `
    <div class="ag-head">
      <span class="ag-title">${esc(d.artist || '(Unknown artist)')}</span>
      <span class="ag-muted">${plural(d.track_count)}</span>
      <button class="btn btn-ghost btn-sm" data-ag="fetch">${mb ? 'Refresh' : 'Fetch genres'}</button>
    </div>
    <div class="ag-row"><span class="ag-label">On tracks</span><div class="ag-chips">${current}</div></div>
    <div class="ag-row"><span class="ag-label">MusicBrainz</span><div class="ag-mb">${mbRow}</div></div>
    ${mb?.genres.length ? `
    <div class="ag-actions">
      <button class="btn btn-primary btn-sm" data-ag="replace">Replace genres on ${plural(d.track_count)}</button>
      <button class="btn btn-ghost btn-sm" data-ag="add">Add to ${plural(d.track_count)}</button>
      <span class="ag-muted" data-ag-count></span>
    </div>` : ''}
  `
  updatePickedCount()
}

function pickedGenres(): string[] {
  return [...artistGenresEl.querySelectorAll<HTMLInputElement>('.ag-pick input:checked')].map(cb => cb.value)
}

function updatePickedCount() {
  const picked = pickedGenres()
  const count = artistGenresEl.querySelector('[data-ag-count]')
  if (count) count.textContent = picked.length ? picked.join('; ') : 'Pick at least one genre'
  artistGenresEl.querySelectorAll<HTMLButtonElement>('[data-ag="replace"], [data-ag="add"]')
    .forEach(b => { b.disabled = !picked.length })
}

async function fetchArtistGenres(mbid?: string) {
  const key = state.selectedArtistKey
  if (key === null) return
  const btn = artistGenresEl.querySelector<HTMLButtonElement>('[data-ag="fetch"]')
  if (btn) { btn.disabled = true; btn.textContent = 'Fetching…' }
  try {
    const detail = await api.artists.fetch(key, mbid)
    if (state.selectedArtistKey !== key) return
    state.artistDetail = detail
    const entry = state.artistEntries.find(a => a.artist === key)
    if (entry && !entry.fetched) { entry.fetched = true; renderArtistsPanel() }
    renderArtistGenres()
  } catch (e) {
    toast(`Fetch failed: ${e}`, 'error')
    renderArtistGenres()
  }
}

async function retagArtist(mode: 'replace' | 'add') {
  const d = state.artistDetail
  const genres = pickedGenres()
  if (!d || !genres.length) return
  const tracks = `${d.track_count.toLocaleString()} track${d.track_count !== 1 ? 's' : ''}`
  const ok = await confirmModal(
    mode === 'replace' ? 'Replace genres' : 'Add genres',
    mode === 'replace'
      ? `Set the genres of ${tracks} by ${d.artist} to “${genres.join('; ')}”? Their current genres are removed. This can be undone.`
      : `Add “${genres.join('; ')}” to ${tracks} by ${d.artist}, keeping their current genres? This can be undone.`,
    mode === 'replace' ? 'Replace' : 'Add',
  )
  if (!ok) return
  try {
    const { job_id } = await api.artists.retag(d.artist, genres, mode)
    scanBtn.disabled = true
    pollScan(job_id)
  } catch (e) {
    toast(String(e).includes('409') ? 'Another job is already running' : `Retag failed: ${e}`, 'error')
  }
}

let fetchAllTimer: ReturnType<typeof setInterval> | null = null

function pollFetchAll(jobId: string) {
  if (fetchAllTimer) clearInterval(fetchAllTimer)
  fetchAllBtn.disabled = true
  fetchAllStatus.hidden = false
  fetchAllStatus.textContent = 'Starting…'
  const tick = async () => {
    try {
      const job = await api.jobs.get(jobId)
      if (job.status === 'pending' || job.status === 'running') {
        fetchAllStatus.textContent = job.total ? `Fetching genres… ${job.scanned}/${job.total}` : 'Starting…'
        return
      }
      clearInterval(fetchAllTimer!); fetchAllTimer = null
      fetchAllBtn.disabled = false
      fetchAllStatus.hidden = true
      toast(job.status === 'done' ? `Fetched genres for ${job.scanned} artist${job.scanned !== 1 ? 's' : ''}` : `Fetch failed: ${job.error}`,
            job.status === 'done' ? 'success' : 'error')
      if (state.sidebarMode === 'artists') { await loadArtists(); await loadArtistDetail() }
    } catch (e) {
      clearInterval(fetchAllTimer!); fetchAllTimer = null
      fetchAllBtn.disabled = false
      fetchAllStatus.hidden = true
    }
  }
  fetchAllTimer = setInterval(tick, 2000)
  tick()
}

// ─── Genres panel ─────────────────────────────────────────────────────────────

function renderGenresPanel() {
  genreListEl.innerHTML = ''

  const allLi = document.createElement('li')
  allLi.className = 'nav-item nav-all' + (state.selectedGenre === null ? ' active' : '')
  allLi.dataset.all = '1'
  allLi.innerHTML = `<span class="nav-icon">♪</span><span class="nav-label">All tracks</span>`
  genreListEl.appendChild(allLi)

  const needle = state.genreFilter.trim().toLowerCase()
  for (const g of state.genres) {
    if (needle && !g.genre.toLowerCase().includes(needle)) continue
    const li = document.createElement('li')
    li.className = 'nav-item nav-genre' + (state.selectedGenre === g.genre ? ' active' : '')
    li.dataset.genre = g.genre
    // Tracks without a genre can be listed, but there's nothing to rename.
    const actions = g.genre ? `
      <span class="nav-actions">
        <button class="nav-action" data-action="rename" title="Rename or merge this genre">✎</button>
        <button class="nav-action" data-action="delete" title="Remove this genre from its tracks">✕</button>
      </span>` : ''
    li.innerHTML = `
      <span class="nav-label">${esc(g.genre || '(No genre)')}</span>
      ${actions}
      <span class="nav-count">${g.track_count}</span>
    `
    genreListEl.appendChild(li)
  }
}

function genreCount(name: string): number {
  return state.genres.find(g => g.genre === name)?.track_count ?? 0
}

function splitGenres(value: string): string[] {
  return [...new Set(value.split(';').map(g => g.trim()).filter(Boolean))]
}

function showRenameGenre(oldName: string) {
  const count = genreCount(oldName)
  const overlay = document.createElement('div')
  overlay.className = 'modal-overlay'
  overlay.innerHTML = `
    <form class="modal-card" id="rg-form">
      <div class="modal-title">Rename genre</div>
      <div class="modal-hint">“${esc(oldName)}” is on ${count} track${count !== 1 ? 's' : ''}.</div>
      <label class="field-label">New name
        <input id="rg-new" type="text" list="genre-options" autocomplete="off" />
      </label>
      <div class="modal-hint" id="rg-note"></div>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" id="rg-cancel">Cancel</button>
        <button type="submit" class="btn btn-primary" id="rg-submit">Rename</button>
      </div>
    </form>
  `
  document.body.appendChild(overlay)
  const close = () => overlay.remove()
  overlay.addEventListener('click', e => { if (e.target === overlay) close() })
  overlay.querySelector('#rg-cancel')!.addEventListener('click', close)
  const input  = overlay.querySelector<HTMLInputElement>('#rg-new')!
  const note   = overlay.querySelector<HTMLElement>('#rg-note')!
  const submit = overlay.querySelector<HTMLButtonElement>('#rg-submit')!
  input.value = oldName
  input.select()

  const updateNote = () => {
    const targets = splitGenres(input.value)
    const merging = targets.filter(g => g !== oldName && genreCount(g) > 0)
    if (!targets.length) {
      note.textContent = 'Empty: the genre is removed from its tracks.'
      submit.textContent = 'Remove'
    } else if (merging.length) {
      note.textContent = `Merges into ${merging.map(g => `“${g}” (${genreCount(g)})`).join(', ')}.`
      submit.textContent = 'Merge'
    } else {
      note.textContent = targets.length > 1 ? `Splits into ${targets.length} genres.` : ''
      submit.textContent = 'Rename'
    }
  }
  input.addEventListener('input', updateNote)
  updateNote()

  overlay.querySelector<HTMLFormElement>('#rg-form')!.addEventListener('submit', async (e) => {
    e.preventDefault()
    const newName = splitGenres(input.value).join('; ')
    if (newName === oldName) { close(); return }
    submit.disabled = true
    if (await applyGenreRename(oldName, newName)) close()
    else submit.disabled = false
  })
}

async function deleteGenre(name: string) {
  const n = genreCount(name)
  const ok = await confirmModal(
    'Remove genre',
    `Remove “${name}” from ${n} track${n !== 1 ? 's' : ''}? Other genres on those tracks are kept. This can be undone.`,
    'Remove',
  )
  if (ok) await applyGenreRename(name, '')
}

async function applyGenreRename(oldName: string, newName: string): Promise<boolean> {
  try {
    const { changed, errors } = await api.tags.renameGenre(oldName, newName)
    const errNote = errors.length ? `, ${errors.length} failed` : ''
    const plural = changed !== 1 ? 's' : ''
    toast(newName
      ? `Renamed “${oldName}” on ${changed} track${plural}${errNote}`
      : `Removed “${oldName}” from ${changed} track${plural}${errNote}`,
      errors.length ? 'error' : 'success')
    if (state.selectedGenre === oldName) state.selectedGenre = splitGenres(newName)[0] ?? null
    state.selectedIds.clear()
    state.page = 0
    await refreshAfterBulk()
    renderEditor()
    return true
  } catch (e) {
    toast(`Genre rename failed: ${e}`, 'error')
    return false
  }
}

// ─── Files panel ─────────────────────────────────────────────────────────────

function renderFilesPanel() {
  const ul = document.createElement('ul')
  ul.className = 'tree-list'

  const allLi = document.createElement('li')
  const allRow = document.createElement('div')
  allRow.className = 'tree-row tree-all' + (!state.selectedDirectory ? ' active' : '')
  allRow.dataset.path = ''
  allRow.innerHTML = `<span class="tree-icon">♪</span><span class="tree-label">All tracks</span>`
  allLi.appendChild(allRow)
  ul.appendChild(allLi)

  if (state.rootNode) ul.appendChild(renderDirNode(state.rootNode, 0))

  dirTreeEl.innerHTML = ''
  dirTreeEl.appendChild(ul)
  updateRescanBtn()
}

function renderDirNode(node: DirNode, depth: number): HTMLElement {
  const li = document.createElement('li')
  const row = document.createElement('div')
  row.className = 'tree-row' + (state.selectedDirectory === node.path ? ' active' : '')
  row.dataset.path = node.path
  row.style.paddingLeft = `${12 + depth * 14}px`

  const arrow = document.createElement('span')
  arrow.className = 'tree-arrow'
  if (node.loading) {
    arrow.textContent = '…'
    arrow.className += ' tree-arrow-loading'
  } else if (node.children === null || node.children.length > 0) {
    arrow.textContent = node.expanded ? '▾' : '▸'
    arrow.dataset.toggle = '1'
  } else {
    arrow.className += ' tree-arrow-empty'
  }

  const icon  = document.createElement('span')
  icon.className = 'tree-icon'
  icon.textContent = node.expanded ? '📂' : '📁'

  const label = document.createElement('span')
  label.className = 'tree-label'
  label.textContent = node.name

  row.append(arrow, icon, label)
  li.appendChild(row)

  if (node.expanded && node.children?.length) {
    const childUl = document.createElement('ul')
    childUl.className = 'tree-list'
    for (const child of node.children) childUl.appendChild(renderDirNode(child, depth + 1))
    li.appendChild(childUl)
  }

  return li
}

// ─── Quality panel ────────────────────────────────────────────────────────────

async function renderQualityPanel() {
  if (!state.qualityIssues) {
    qualityListEl.innerHTML = '<li class="nav-item" style="color:var(--text-muted);font-size:12px;padding:10px 12px">Loading…</li>'
    try {
      state.qualityIssues = await api.library.issues()
    } catch (e) {
      toast(`Failed to load quality report: ${e}`, 'error')
      return
    }
  }

  qualityListEl.innerHTML = ''
  let anyIssues = false

  for (const issue of QUALITY_ISSUES) {
    const count = state.qualityIssues[issue.key] ?? 0
    if (count === 0) continue
    anyIssues = true
    const li = document.createElement('li')
    const isActive = state.selectedIssue === issue.key
    li.className = 'nav-item quality-issue-item' + (isActive ? ' active' : '')
    li.dataset.issue = issue.key
    li.innerHTML = `
      <span class="quality-issue-dot${issue.warn ? ' quality-issue-dot-warn' : ''}"></span>
      <span class="nav-label">${issue.label}</span>
      <span class="nav-count">${count}</span>
    `
    qualityListEl.appendChild(li)
  }

  if (!anyIssues) {
    qualityListEl.innerHTML = '<li class="nav-item quality-all-good">✓ All tags look good</li>'
  }
}

// ─── Album grid ───────────────────────────────────────────────────────────────

function getVisibleAlbums(): Album[] {
  if (state.sidebarMode === 'tags' && state.selectedArtist) {
    return state.albumsByArtist.get(state.selectedArtist) ?? []
  }
  const all: Album[] = []
  for (const albs of state.albumsByArtist.values()) all.push(...albs)
  return all
}

function makeAlbumCard(alb: Album): HTMLElement {
  const card = document.createElement('div')
  card.className = 'album-card'
  const artistDisplay = esc(alb.album_artist || alb.artist || '(Unknown Artist)')
  card.innerHTML = `
    <div class="album-cover-wrap">
      <img class="album-cover" loading="lazy" src="${coverUrl(alb.cover_track_id)}" alt="${esc(alb.album || '')}" />
      <div class="album-cover-placeholder">♪</div>
    </div>
    <div class="album-info">
      <div class="album-title">${esc(alb.album || '(Unknown Album)')}</div>
      <div class="album-artist">${artistDisplay}</div>
      <div class="album-count">${alb.track_count} track${alb.track_count !== 1 ? 's' : ''}</div>
    </div>
  `
  const img = card.querySelector<HTMLImageElement>('.album-cover')!
  const placeholder = card.querySelector<HTMLElement>('.album-cover-placeholder')!
  img.addEventListener('error', () => { img.style.display = 'none'; placeholder.style.display = 'flex' })
  img.addEventListener('load',  () => { img.style.display = 'block'; placeholder.style.display = 'none' })
  card.addEventListener('click', () => {
    state.viewMode = 'list'
    renderViewMode()
    navigateTo(alb.artist ?? '', alb.album)
  })
  return card
}

// Render album cards in batches, appending more as the user scrolls, so a
// library with thousands of albums doesn't build every card up front.
const ALBUM_BATCH = 60
let albumObserver: IntersectionObserver | null = null

function renderAlbumGrid() {
  albumObserver?.disconnect()
  albumGridEl.innerHTML = ''
  const albums = getVisibleAlbums()

  if (!albums.length) {
    albumGridEl.innerHTML = '<div class="album-empty">No albums found.</div>'
    return
  }

  const sentinel = document.createElement('div')
  sentinel.className = 'album-sentinel'
  albumGridEl.appendChild(sentinel)

  let rendered = 0
  const renderMore = () => {
    const next = albums.slice(rendered, rendered + ALBUM_BATCH)
    for (const alb of next) albumGridEl.insertBefore(makeAlbumCard(alb), sentinel)
    rendered += next.length
    if (rendered >= albums.length) { albumObserver?.disconnect(); sentinel.remove() }
  }
  renderMore()

  if (rendered < albums.length) {
    albumObserver = new IntersectionObserver(
      entries => { if (entries.some(e => e.isIntersecting)) renderMore() },
      { root: albumGridEl, rootMargin: '600px' },
    )
    albumObserver.observe(sentinel)
  }
}

function renderViewMode() {
  tableWrapEl.style.display  = state.viewMode === 'list'   ? ''     : 'none'
  albumGridEl.style.display  = state.viewMode === 'albums' ? 'grid' : 'none'
  viewListBtn.classList.toggle('active',   state.viewMode === 'list')
  viewAlbumsBtn.classList.toggle('active', state.viewMode === 'albums')
  if (state.viewMode === 'albums') renderAlbumGrid()
}

// ─── Column picker ────────────────────────────────────────────────────────────

function renderColPicker() {
  colPickerEl.innerHTML = ''
  for (const col of COL_DEFS) {
    const label = document.createElement('label')
    label.className = 'col-picker-item'
    const cb = document.createElement('input')
    cb.type = 'checkbox'
    cb.checked = state.visibleCols.has(col.key)
    cb.dataset.col = col.key
    label.appendChild(cb)
    label.append(' ' + col.label)
    colPickerEl.appendChild(label)
  }
}

// ─── Sorting and filtering ─────────────────────────────────────────────────────

function getSortedFilteredTracks(): Track[] {
  let result = [...state.tracks]

  if (state.filterFormat) {
    result = result.filter(t => t.format === state.filterFormat)
  }
  if (state.filterQuality) {
    result = result.filter(t => trackQuality(t) === state.filterQuality)
  }

  if (state.sortKey) {
    const key = state.sortKey
    const dir = state.sortDir === 'asc' ? 1 : -1
    result.sort((a, b) => {
      const av = (a[key as keyof Track] as string | null | undefined) ?? null
      const bv = (b[key as keyof Track] as string | null | undefined) ?? null
      if (av === null && bv === null) return 0
      if (av === null) return 1
      if (bv === null) return -1
      return String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: 'base' }) * dir
    })
  }

  return result
}

function renderFormatOptions() {
  const formats = [...new Set(state.tracks.map(t => t.format))].sort()
  const current = filterFormatEl.value
  filterFormatEl.innerHTML = '<option value="">Format</option>'
  for (const fmt of formats) {
    const opt = document.createElement('option')
    opt.value = fmt
    opt.textContent = fmt.toUpperCase()
    filterFormatEl.appendChild(opt)
  }
  if (formats.includes(current)) filterFormatEl.value = current
}

// ─── Keyboard navigation ──────────────────────────────────────────────────────

function navigateTrack(dir: 1 | -1) {
  const displayedTracks = getSortedFilteredTracks()
  if (!displayedTracks.length) return
  const lastId = [...state.selectedIds][state.selectedIds.size - 1]
  const idx = displayedTracks.findIndex(t => t.id === lastId)
  const nextIdx = Math.max(0, Math.min(displayedTracks.length - 1, idx + dir))
  const nextTrack = displayedTracks[nextIdx]
  state.selectedIds.clear()
  state.selectedIds.add(nextTrack.id)
  renderTracks()
  renderEditor()
  const tr = trackTbody.querySelector<HTMLTableRowElement>(`tr[data-id="${nextTrack.id}"]`)
  tr?.scrollIntoView({ block: 'nearest' })
}

// ─── Track table ──────────────────────────────────────────────────────────────

function renderColHeaders() {
  const qualityTh = state.visibleCols.has('quality') ? '<th class="col-quality"></th>' : ''
  trackTheadRow.innerHTML = `${qualityTh}<th class="col-check"><input type="checkbox" id="select-all" /></th>`
  for (const col of COL_DEFS) {
    if (col.key === 'quality') continue
    if (!state.visibleCols.has(col.key)) continue
    const th = document.createElement('th')
    th.className = col.cls
    th.dataset.sort = col.key
    th.style.cursor = 'pointer'
    let label = col.label
    if (state.sortKey === col.key) {
      label += state.sortDir === 'asc' ? ' ↑' : ' ↓'
    }
    th.textContent = label
    th.addEventListener('click', () => {
      if (state.sortKey === col.key) {
        state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc'
      } else {
        state.sortKey = col.key
        state.sortDir = 'asc'
      }
      renderColHeaders()
      renderTracks()
    })
    trackTheadRow.appendChild(th)
  }
  selectAll()?.addEventListener('change', onSelectAll)
}

function renderTracks() {
  trackTbody.innerHTML = ''

  if (!state.tracks.length) {
    const pristine = state.total === 0 && !state.query && !state.artists.length
      && state.selectedArtist === null && state.selectedAlbum === null
      && state.selectedDirectory === null && !state.selectedIssue
    trackEmpty.innerHTML = pristine
      ? 'Your library is empty. Click <strong>Scan Library</strong> to index your music.'
      : 'No tracks found.'
    trackEmpty.hidden = false
    trackLoading.hidden = true
    trackCount.textContent = '0 tracks'
    updateBulkBar()
    return
  }

  trackEmpty.hidden = true
  trackLoading.hidden = true

  const displayedTracks = getSortedFilteredTracks()
  const filtersActive = !!(state.filterFormat || state.filterQuality)
  if (filtersActive) {
    trackCount.textContent = `${displayedTracks.length.toLocaleString()} of ${state.total.toLocaleString()} track${state.total !== 1 ? 's' : ''}`
  } else {
    trackCount.textContent = `${state.total.toLocaleString()} track${state.total !== 1 ? 's' : ''}`
  }

  const visibleCols = COL_DEFS.filter(c => state.visibleCols.has(c.key))

  for (const t of displayedTracks) {
    const selected = state.selectedIds.has(t.id)
    const tr = document.createElement('tr')
    tr.dataset.id = String(t.id)
    if (selected) tr.classList.add('selected')
    const q = trackQuality(t)
    const qualityCell = state.visibleCols.has('quality')
      ? `<td class="col-quality"><span class="quality-dot quality-dot-${q}" title="${QUALITY_TITLES[q]}"></span></td>`
      : ''
    let cells = `${qualityCell}<td class="col-check"><input type="checkbox"${selected ? ' checked' : ''} /></td>`
    for (const col of visibleCols) {
      if (col.key === 'quality') continue
      cells += `<td class="${col.cls}">${col.render(t)}</td>`
    }
    tr.innerHTML = cells
    tr.querySelectorAll<HTMLElement>('.tag-link').forEach(link => {
      link.addEventListener('click', (e) => {
        e.stopPropagation()
        navigateTo(link.dataset.artist ?? '', link.dataset.album)
      })
    })
    trackTbody.appendChild(tr)
  }

  const sa = selectAll()
  if (sa) {
    const allSel = state.tracks.every(t => state.selectedIds.has(t.id))
    const anySel = state.tracks.some(t => state.selectedIds.has(t.id))
    sa.checked       = allSel
    sa.indeterminate = !allSel && anySel
  }

  updateBulkBar()
}

function updateBulkBar() {
  const n = state.selectedIds.size
  bulkActions.hidden = n === 0
  selectionCount.textContent = `${n.toLocaleString()} selected`
  removeTracksBtn.style.display = n > 0 ? '' : 'none'
  replaygainBtn.hidden = !(n > 0 && replaygainAvailable)

  // Whole page selected but the view has more pages: offer to select them all.
  // Format/quality filters only apply to the loaded page, so skip it then.
  const pageSelected = state.tracks.length > 0 && state.tracks.every(t => state.selectedIds.has(t.id))
  const canSelectMore = pageSelected && n < state.total && state.total > state.tracks.length
    && !state.filterFormat && !state.filterQuality
  selectMatchingBtn.hidden = !canSelectMore
  if (canSelectMore) selectMatchingBtn.textContent = `Select all ${state.total.toLocaleString()} tracks`
}

async function selectAllMatching() {
  selectMatchingBtn.disabled = true
  try {
    const ids = await api.library.trackIds(currentViewParams())
    state.selectedIds = new Set(ids)
    renderTracks()
    renderEditor()
  } catch (e) {
    toast(`Selection failed: ${e}`, 'error')
  } finally {
    selectMatchingBtn.disabled = false
  }
}

// ─── Pagination ───────────────────────────────────────────────────────────────

const paginationEl = document.getElementById('pagination')!

function renderPagination() {
  const totalPages = Math.ceil(state.total / PAGE_SIZE)
  if (totalPages <= 1) { paginationEl.hidden = true; return }
  paginationEl.hidden = false
  const start = state.page * PAGE_SIZE + 1
  const end   = Math.min((state.page + 1) * PAGE_SIZE, state.total)
  paginationEl.innerHTML = `
    <button class="btn btn-ghost btn-sm" id="page-prev" ${state.page === 0 ? 'disabled' : ''}>← Prev</button>
    <span class="page-info">${start.toLocaleString()}–${end.toLocaleString()} of ${state.total.toLocaleString()}</span>
    <button class="btn btn-ghost btn-sm" id="page-next" ${state.page >= totalPages - 1 ? 'disabled' : ''}>Next →</button>
  `
  document.getElementById('page-prev')!.addEventListener('click', async () => {
    state.page--; await loadTracks()
    paginationEl.scrollIntoView({ block: 'nearest' })
  })
  document.getElementById('page-next')!.addEventListener('click', async () => {
    state.page++; await loadTracks()
    paginationEl.scrollIntoView({ block: 'nearest' })
  })
}

// ─── Tag editor ───────────────────────────────────────────────────────────────

function renderEditor() {
  if (state.selectedIds.size === 0) { tagEditor.hidden = true; editorResizer.hidden = true; lookupPanel.hidden = true; state.pendingCoverAlbumId = null; state.pendingLookupResult = null; playerEl.pause(); playerEl.hidden = true; editorRenamePreviewEl.hidden = true; return }
  tagEditor.hidden = false
  editorResizer.hidden = false
  // Only show lookup/infer for single selection; hide panel when selection changes
  lookupBtn.hidden = state.selectedIds.size !== 1
  inferBtn.hidden = state.selectedIds.size !== 1
  autoFixBtn.hidden = state.selectedIds.size === 0
  if (state.selectedIds.size !== 1) lookupPanel.hidden = true
  const sel = state.tracks.filter(t => state.selectedIds.has(t.id))
  const n = state.selectedIds.size
  editorTitle.textContent = n === 1 && sel.length ? (sel[0].title || sel[0].filename) : `${n.toLocaleString()} tracks`
  populateForm(sel)
  updateCoverPreview()
  updatePlayer()
  updateEditorRenamePreview()
  updateSpectro()
}

function updateSpectro() {
  // Shown for a single selection; collapsed by default (rendering is lazy).
  const show = state.selectedIds.size === 1 && spectrogramAvailable
  spectroSection.hidden = !show
  spectroWrap.hidden = true
  spectroImg.removeAttribute('src')
  spectroImg.style.display = 'none'
  spectroPopout.style.display = 'none'
  spectroBtn.textContent = 'Spectrogram ▾'
}

function toggleSpectro() {
  if (state.selectedIds.size !== 1) return
  if (!spectroWrap.hidden) {
    spectroWrap.hidden = true
    spectroBtn.textContent = 'Spectrogram ▾'
    return
  }
  spectroWrap.hidden = false
  spectroBtn.textContent = 'Spectrogram ▴'
  const url = api.spectrogram.url([...state.selectedIds][0])
  if (spectroImg.getAttribute('src') !== url) {
    spectroStatus.textContent = 'Rendering…'
    spectroImg.style.display = 'none'
    spectroPopout.style.display = 'none'
    spectroImg.onload = () => {
      spectroImg.style.display = 'block'
      spectroPopout.style.display = 'block'
      spectroStatus.textContent = ''
    }
    spectroImg.onerror = () => { spectroStatus.textContent = 'Could not render spectrogram' }
    spectroImg.src = url
  } else if (spectroImg.getAttribute('src')) {
    spectroPopout.style.display = 'block'
  }
}

// Open the rendered spectrogram at full size in a dismissible lightbox.
function openSpectroLightbox() {
  const src = spectroImg.getAttribute('src')
  if (!src || spectroImg.style.display === 'none') return

  const overlay = document.createElement('div')
  overlay.className = 'spectro-lightbox-overlay'

  const box = document.createElement('div')
  box.className = 'spectro-lightbox'

  const img = document.createElement('img')
  img.src = src
  img.alt = 'Frequency spectrogram'

  const cap = document.createElement('div')
  cap.className = 'spectro-lightbox-cap'
  cap.textContent = editorTitle.textContent || ''

  const close = document.createElement('button')
  close.type = 'button'
  close.className = 'spectro-lightbox-close'
  close.setAttribute('aria-label', 'Close')
  close.textContent = '✕'

  box.append(img, cap, close)
  overlay.appendChild(box)

  const dismiss = () => { overlay.remove(); document.removeEventListener('keydown', onKey) }
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') dismiss() }
  overlay.addEventListener('click', e => { if (e.target === overlay) dismiss() })
  close.addEventListener('click', dismiss)
  document.addEventListener('keydown', onKey)

  document.body.appendChild(overlay)
}

const updateEditorRenamePreview = debounce(async () => {
  if (state.selectedIds.size !== 1 || !renameOnSave || !renameTemplate) {
    editorRenamePreviewEl.hidden = true
    return
  }
  const tags: Record<string, string> = {}
  for (const field of TAG_FIELDS) {
    const el = tagForm.elements.namedItem(field) as HTMLInputElement | HTMLTextAreaElement | null
    if (el) tags[field] = el.value
  }
  const track = state.tracks.find(t => state.selectedIds.has(t.id))
  const ext = track ? '.' + (track.filename.split('.').pop() || 'flac') : '.flac'
  try {
    const res = await api.settings.renamePreview(renameTemplate, tags, ext)
    editorRenamePreviewEl.hidden = false
    editorRenamePreviewEl.textContent = res.ok ? 'Saves to: ' + res.preview : (res.error ?? '')
    editorRenamePreviewEl.classList.toggle('rename-preview-error', !res.ok)
  } catch {
    editorRenamePreviewEl.hidden = true
  }
}, 250)

function updatePlayer() {
  if (state.selectedIds.size === 1) {
    const url = `/api/stream/${[...state.selectedIds][0]}`
    if (playerEl.getAttribute('src') !== url) playerEl.src = url
    playerEl.hidden = false
  } else {
    playerEl.pause()
    playerEl.removeAttribute('src')
    playerEl.load()
    playerEl.hidden = true
  }
}

function updateCoverPreview() {
  if (state.selectedIds.size === 0) return
  const firstId = [...state.selectedIds][0]
  coverPlaceholder.style.display = 'flex'
  coverImg.style.display = 'none'
  coverImg.src = coverUrl(firstId)
  coverImg.onload  = () => { coverImg.style.display = 'block'; coverPlaceholder.style.display = 'none' }
  coverImg.onerror = () => { coverImg.style.display = 'none';  coverPlaceholder.style.display = 'flex' }
}

const compilationCheckbox = () => tagForm.elements.namedItem('compilation') as HTMLInputElement

function populateForm(tracks: Track[]) {
  // Selected tracks off the loaded page have unknown values: show every field
  // as mixed so saving only writes the fields the user actually fills in.
  const partial = tracks.length < state.selectedIds.size
  for (const field of TAG_FIELDS) {
    const el = tagForm.elements.namedItem(field) as HTMLInputElement | HTMLTextAreaElement | null
    if (!el) continue
    const vals = tracks.map(t => (t[field as keyof Track] as string | null) ?? '')
    const allSame = !partial && vals.every(v => v === vals[0])
    if (allSame) {
      el.value = vals[0]; el.placeholder = ''; delete (el as HTMLElement).dataset.mixed
    } else {
      el.value = ''; el.placeholder = '(multiple values)'; (el as HTMLElement).dataset.mixed = '1'
    }
  }
  // compilation: boolean checkbox; indeterminate = mixed across the selection.
  const comp = tracks.map(t => t.compilation === '1')
  const compSame = !partial && comp.every(v => v === comp[0])
  const cb = compilationCheckbox()
  cb.indeterminate = !compSame
  cb.checked = compSame ? comp[0] : false

  genreModeEl.hidden = state.selectedIds.size < 2
  genreModeEl.value = 'replace'
}

function genreInput(): HTMLInputElement {
  return tagForm.elements.namedItem('genre') as HTMLInputElement
}

function onGenreModeChange() {
  if (genreModeEl.value === 'replace') {
    populateForm(state.tracks.filter(t => state.selectedIds.has(t.id)))
    return
  }
  const el = genreInput()
  el.value = ''
  delete el.dataset.mixed
  el.placeholder = genreModeEl.value === 'add' ? 'Genres to add, separated by ;' : 'Genres to remove, separated by ;'
  el.focus()
}

// ─── MusicBrainz lookup ───────────────────────────────────────────────────────

const SOURCE_BADGES: Record<string, { cls: string; label: string }> = {
  acoustid: { cls: 'source-acoustid', label: 'AcoustID' },
  discogs:  { cls: 'source-discogs',  label: 'Discogs' },
  filename: { cls: 'source-filename', label: 'Filename' },
  musicbrainz: { cls: 'source-mb', label: 'MusicBrainz' },
}

function renderSourceBadge(source: string): string {
  const b = SOURCE_BADGES[source] ?? SOURCE_BADGES.musicbrainz
  return `<span class="lookup-source-badge ${b.cls}">${b.label}</span>`
}

function attachEditions(li: HTMLElement, r: LookupResult) {
  const btn = li.querySelector<HTMLButtonElement>('.lookup-editions-btn')
  if (!btn || !r.mb_track_id) return
  let panel: HTMLElement | null = null
  let loaded = false
  btn.addEventListener('click', async (e) => {
    e.stopPropagation()
    if (panel) { panel.hidden = !panel.hidden; return }
    panel = document.createElement('ul')
    panel.className = 'lookup-editions'
    panel.innerHTML = '<li class="lookup-editions-note">Loading editions…</li>'
    li.appendChild(panel)
    try {
      const releases = await api.lookup.releases(r.mb_track_id!)
      loaded = true
      if (!releases.length) { panel.innerHTML = '<li class="lookup-editions-note">No releases found</li>'; return }
      panel.innerHTML = ''
      for (const rel of releases) {
        const row = document.createElement('li')
        row.className = 'lookup-edition'
        const bits = [rel.year, rel.country, rel.format, rel.track_count ? `${rel.track_count} tracks` : null]
          .filter(Boolean).join(' · ')
        row.innerHTML = `<span class="lookup-edition-album">${esc(rel.album || '(unknown)')}</span><span class="lookup-edition-meta">${esc(bits)}</span>`
        row.addEventListener('click', (ev) => {
          ev.stopPropagation()
          applyLookupResult({ ...r, album: rel.album, year: rel.year, mb_album_id: rel.mb_album_id })
        })
        panel.appendChild(row)
      }
    } catch (err) {
      if (!loaded) panel.innerHTML = `<li class="lookup-editions-note">Error: ${esc(String(err))}</li>`
    }
  })
}

async function runLookup() {
  if (state.selectedIds.size !== 1) return
  const trackId = [...state.selectedIds][0]

  lookupBtn.disabled = true
  lookupBtn.textContent = 'Looking up…'
  lookupPanel.hidden = false
  lookupResults.innerHTML = '<li class="lookup-searching">Searching MusicBrainz…</li>'

  try {
    const results = await api.lookup.search(trackId)
    lookupResults.innerHTML = ''

    if (!results.length) {
      lookupResults.innerHTML = '<li class="lookup-empty">No results found.</li>'
      return
    }

    for (const r of results) {
      const li = document.createElement('li')
      li.className = 'lookup-result'
      const pct = Math.round(r.score * 100)
      const scoreClass = pct >= 90 ? 'score-high' : pct >= 70 ? 'score-mid' : 'score-low'
      const sourceBadge = renderSourceBadge(r.source)
      const thumbHtml = r.mb_album_id
        ? `<img class="lookup-thumb" loading="lazy" src="https://coverartarchive.org/release/${r.mb_album_id}/front-250" alt="" />`
        : `<div class="lookup-thumb lookup-thumb-empty">♪</div>`
      const canPickEdition = r.source === 'musicbrainz' && !!r.mb_track_id
      const editionsBtn = canPickEdition
        ? `<button class="lookup-editions-btn" title="Choose a specific release/edition">Editions ▾</button>`
        : ''
      li.innerHTML = `
        ${thumbHtml}
        <span class="lookup-score ${scoreClass}">${pct}%</span>
        <span class="lookup-info">
          <span class="lookup-track-title">${esc(r.title || '(unknown)')} ${sourceBadge}</span>
          <span class="lookup-meta">${esc(r.artist || '')}${r.album ? ' · ' + esc(r.album) : ''}${r.year ? ' · ' + esc(r.year) : ''} ${editionsBtn}</span>
        </span>
      `
      // Hide broken thumbnails gracefully
      li.querySelector<HTMLImageElement>('.lookup-thumb')
        ?.addEventListener('error', function() { this.style.display = 'none' })
      li.addEventListener('click', () => applyLookupResult(r))
      if (canPickEdition) attachEditions(li, r)
      lookupResults.appendChild(li)
    }
  } catch (e) {
    lookupResults.innerHTML = `<li class="lookup-empty">Error: ${esc(String(e))}</li>`
  } finally {
    lookupBtn.disabled = false
    lookupBtn.textContent = 'Lookup'
  }
}

function applyLookupResult(r: LookupResult) {
  const fields: (keyof LookupResult)[] = ['title', 'artist', 'album', 'album_artist', 'year', 'track_number', 'disc_number']
  for (const field of fields) {
    const el = tagForm.elements.namedItem(field) as HTMLInputElement | null
    if (!el) continue
    el.value = (r[field] as string | null) ?? ''
    delete el.dataset.mixed
  }

  // Store full lookup result so MB IDs are included on save
  state.pendingLookupResult = r
  state.pendingCoverAlbumId = r.mb_album_id
  if (r.mb_album_id) {
    coverPlaceholder.style.display = 'none'
    coverImg.style.display = 'block'
    coverImg.src = `https://coverartarchive.org/release/${r.mb_album_id}/front-250`
    coverImg.onerror = () => { coverImg.style.display = 'none'; coverPlaceholder.style.display = 'flex' }
  }

  lookupPanel.hidden = true
  const coverMsg = r.mb_album_id ? ' + cover art' : ''
  toast(`Applied${coverMsg} — review and save to write to file`, 'info')
}

// ─── Auto-fix ─────────────────────────────────────────────────────────────────

const FIX_SAVE_FIELDS: (keyof LookupResult)[] = [
  'title', 'artist', 'album', 'album_artist', 'year', 'track_number', 'disc_number',
  'mb_track_id', 'mb_artist_id', 'mb_album_id', 'mb_album_artist_id',
]

const FIX_DISPLAY_FIELDS: { key: keyof LookupResult; label: string }[] = [
  { key: 'title',        label: 'Title'        },
  { key: 'artist',       label: 'Artist'       },
  { key: 'album',        label: 'Album'        },
  { key: 'album_artist', label: 'Album Artist' },
  { key: 'year',         label: 'Year'         },
  { key: 'track_number', label: 'Track #'      },
  { key: 'disc_number',  label: 'Disc #'       },
]

interface FixProposal {
  track:  Track
  score:  number
  source: string
  update: Record<string, string>
}

async function buildProposal(track: Track): Promise<FixProposal | null> {
  const results = await api.lookup.search(track.id)
  if (!results.length) return null
  const top = results[0]
  const update: Record<string, string> = {}
  for (const f of FIX_SAVE_FIELDS) {
    const v = top[f]
    if (v != null) update[f as string] = v as string
  }
  if (!Object.keys(update).length) return null
  return { track, score: top.score, source: top.source, update }
}

function showFixConfirmation(proposals: FixProposal[], onConfirm: (p: FixProposal[]) => Promise<void>) {
  const overlay = document.createElement('div')
  overlay.className = 'fix-confirm-overlay'

  const modal = document.createElement('div')
  modal.className = 'fix-confirm-modal'

  const header = document.createElement('div')
  header.className = 'fix-confirm-header'
  header.innerHTML = `
    <span class="fix-confirm-title">Auto-fix — ${proposals.length} track${proposals.length !== 1 ? 's' : ''}</span>
    <button class="btn btn-ghost btn-icon fix-confirm-close">✕</button>
  `

  const body = document.createElement('div')
  body.className = 'fix-confirm-body'

  for (const p of proposals) {
    const pct = Math.round(p.score * 100)
    const scoreClass = pct >= 90 ? 'score-high' : pct >= 70 ? 'score-mid' : 'score-low'
    const rows = FIX_DISPLAY_FIELDS
      .filter(f => p.update[f.key as string] != null)
      .map(f => {
        const proposed = p.update[f.key as string]
        const current  = (p.track[f.key as keyof Track] as string | null) ?? ''
        return `<tr>
          <td class="fix-col-field">${esc(f.label)}</td>
          <td class="fix-col-current">${current ? esc(current) : '<span class="fix-empty">empty</span>'}</td>
          <td class="fix-col-arrow">→</td>
          <td class="fix-col-proposed">${esc(proposed)}</td>
        </tr>`
      }).join('')

    const section = document.createElement('div')
    section.className = 'fix-confirm-track'
    section.innerHTML = `
      <div class="fix-confirm-track-header">
        <span class="fix-confirm-track-name">${esc(p.track.title || p.track.filename)}</span>
        <span class="lookup-score ${scoreClass}">${pct}%</span>
        <span class="fix-confirm-source">${esc(p.source)}</span>
      </div>
      ${rows ? `<table class="fix-confirm-table"><tbody>${rows}</tbody></table>`
              : '<p class="fix-empty-note">No visible tag changes — only metadata IDs will be updated.</p>'}
    `
    body.appendChild(section)
  }

  const footer = document.createElement('div')
  footer.className = 'fix-confirm-footer'
  const cancelBtn = document.createElement('button')
  cancelBtn.className = 'btn btn-ghost'
  cancelBtn.textContent = 'Cancel'
  const applyBtn = document.createElement('button')
  applyBtn.className = 'btn btn-primary'
  applyBtn.textContent = `Apply ${proposals.length} change${proposals.length !== 1 ? 's' : ''}`
  footer.append(cancelBtn, applyBtn)

  modal.append(header, body, footer)
  overlay.appendChild(modal)
  document.body.appendChild(overlay)

  const close = () => overlay.remove()
  cancelBtn.addEventListener('click', close)
  header.querySelector<HTMLElement>('.fix-confirm-close')!.addEventListener('click', close)
  overlay.addEventListener('click', e => { if (e.target === overlay) close() })

  applyBtn.addEventListener('click', async () => {
    applyBtn.disabled = true
    applyBtn.textContent = 'Applying…'
    try { await onConfirm(proposals) } finally { close() }
  })
}

async function applyProposals(proposals: FixProposal[]) {
  let saved = 0
  for (const p of proposals) {
    try { await api.tags.update(p.track.id, p.update); saved++ } catch { /* skip */ }
  }
  toast(`Saved ${saved} track${saved !== 1 ? 's' : ''}`, 'success')
  state.qualityIssues = null
  await reloadNav()
  await loadTracks()
  if (state.sidebarMode === 'quality') await renderQualityPanel()
  const updated = state.tracks.filter(t => state.selectedIds.has(t.id))
  if (updated.length) populateForm(updated)
  await refreshUndoButton()
}

async function gatherProposals(tracks: Track[], setLabel: (s: string) => void): Promise<FixProposal[]> {
  const proposals: FixProposal[] = []
  for (let i = 0; i < tracks.length; i++) {
    setLabel(tracks.length === 1 ? 'Looking up…' : `Looking up ${i + 1}/${tracks.length}…`)
    try { const p = await buildProposal(tracks[i]); if (p) proposals.push(p) } catch { /* skip */ }
  }
  return proposals
}

const autoFixBtn = document.getElementById('auto-fix-btn') as HTMLButtonElement

autoFixBtn.addEventListener('click', async () => {
  const tracks = state.tracks.filter(t => state.selectedIds.has(t.id))
  if (!tracks.length) return
  autoFixBtn.disabled = true
  const proposals = await gatherProposals(tracks, s => { autoFixBtn.textContent = s })
  autoFixBtn.disabled = false
  autoFixBtn.textContent = 'Auto-fix'
  if (!proposals.length) { toast('No matches found', 'info'); return }
  showFixConfirmation(proposals, applyProposals)
})

const fixAllBtn      = document.getElementById('fix-all-btn') as HTMLButtonElement
const dedupeBtn      = document.getElementById('dedupe-btn') as HTMLButtonElement
const qualityToolbar = document.getElementById('quality-toolbar') as HTMLElement

dedupeBtn.addEventListener('click', async () => {
  dedupeBtn.disabled = true
  try {
    const { removed } = await api.library.dedupeKeepBest()
    if (removed === 0) {
      toast('No duplicates to remove', 'info')
    } else {
      toast(`Removed ${removed} duplicate${removed !== 1 ? 's' : ''} — kept best quality`, 'success')
      state.qualityIssues = null
      await loadTracks()
      await renderQualityPanel()
      await refreshUndoButton()
    }
  } catch (e) {
    toast(`Dedupe failed: ${e}`, 'error')
  } finally {
    dedupeBtn.disabled = false
  }
})

fixAllBtn.addEventListener('click', async () => {
  const tracks = [...state.tracks]
  if (!tracks.length) return
  fixAllBtn.disabled = true
  const proposals = await gatherProposals(tracks, s => { fixAllBtn.textContent = s })
  fixAllBtn.disabled = false
  fixAllBtn.textContent = 'Fix All'
  if (!proposals.length) { toast('No matches found', 'info'); return }
  showFixConfirmation(proposals, applyProposals)
})

// ─── Scan status ──────────────────────────────────────────────────────────────

function renderScanStatus() {
  const job = state.scanJob
  if (!job || job.status === 'done' || job.status === 'error') { scanStatusEl.textContent = ''; return }
  const pct = job.total ? Math.round((job.scanned / job.total) * 100) : 0
  const verb = { scan: 'Scanning', unify: 'Unifying albums', undo: 'Undoing', retag: 'Retagging', fetch: 'Fetching' }[job.kind] ?? 'Working'
  scanStatusEl.textContent = job.status !== 'running'
    ? `${verb}…`
    : job.kind === 'undo'
      ? `${verb} ${job.total} tracks…`
      : `${verb}… ${job.scanned}/${job.total} (${pct}%)`
}

// ─── Data loading ─────────────────────────────────────────────────────────────

async function loadLibrary() {
  try {
    const [artists, albums] = await Promise.all([
      api.library.artists(),
      api.library.albums(),
    ])
    state.artists = artists
    // Group albums by artist; expand all artists by default
    state.albumsByArtist = new Map()
    for (const alb of albums) {
      const key = alb.artist ?? ''
      if (!state.albumsByArtist.has(key)) {
        state.albumsByArtist.set(key, [])
      }
      state.albumsByArtist.get(key)!.push(alb)
    }
    renderTagsPanel()
  } catch (e) {
    toast(`Failed to load library: ${e}`, 'error')
  }
}

async function loadGenres() {
  try {
    state.genres = await api.library.genres()
    genreOptionsEl.innerHTML = state.genres
      .filter(g => g.genre)
      .map(g => `<option value="${esc(g.genre)}"></option>`)
      .join('')
    renderGenresPanel()
  } catch (e) {
    toast(`Failed to load genres: ${e}`, 'error')
  }
}

// Reload the tag-derived sidebar lists after tags change. Genres always
// reload: they also feed the editor's autocomplete.
async function reloadNav() {
  if (state.sidebarMode === 'tags') await loadLibrary()
  await loadGenres()
}

async function loadTree() {
  try {
    const result = await api.fs.tree()
    const name = result.path
      ? (result.path.split('/').filter(Boolean).pop() ?? result.path)
      : 'Music Library'
    state.rootNode = {
      name,
      path:     result.path,
      children: result.dirs.map(d => ({ name: d.name, path: d.path, children: null, loading: false, expanded: false })),
      loading:  false,
      expanded: true,
    }
    renderFilesPanel()
  } catch (e) {
    toast(`Failed to load directory tree: ${e}`, 'error')
  }
}

async function expandDirNode(node: DirNode) {
  if (node.loading) return
  if (node.children !== null) {
    node.expanded = !node.expanded
    renderFilesPanel()
    return
  }
  node.loading = true
  renderFilesPanel()
  try {
    const result = await api.fs.tree(node.path)
    node.children = result.dirs.map(d => ({ name: d.name, path: d.path, children: null, loading: false, expanded: false }))
    node.expanded  = true
    node.loading   = false
  } catch (e) {
    node.loading = false
    toast(`Failed to expand directory: ${e}`, 'error')
  }
  renderFilesPanel()
}

async function loadTracks() {
  trackLoading.hidden = false
  trackTbody.innerHTML = ''
  trackEmpty.hidden = true

  const offset = state.page * PAGE_SIZE

  try {
    let result
    if (state.query) {
      result = await api.library.search(state.query, PAGE_SIZE, offset)
    } else if (state.sidebarMode === 'files') {
      const params: Record<string, string | number> = { limit: PAGE_SIZE, offset }
      if (state.selectedDirectory !== null) params.directory = state.selectedDirectory
      result = await api.library.tracks(params)
    } else if (state.sidebarMode === 'genres') {
      const params: Record<string, string | number> = { limit: PAGE_SIZE, offset }
      if (state.selectedGenre !== null) params.genre = state.selectedGenre
      result = await api.library.tracks(params)
    } else if (state.sidebarMode === 'artists') {
      const params: Record<string, string | number> = { limit: PAGE_SIZE, offset }
      if (state.selectedArtistKey !== null) params.artist_key = state.selectedArtistKey
      result = await api.library.tracks(params)
    } else if (state.sidebarMode === 'quality') {
      if (!state.selectedIssue) {
        state.tracks = []; state.total = 0; renderTracks(); renderPagination(); return
      }
      if (state.selectedIssue === 'missing_files') {
        result = await api.library.dead()
      } else {
        result = await api.library.tracks({ issue: state.selectedIssue, limit: PAGE_SIZE, offset })
      }
    } else {
      const params: Record<string, string | number> = { limit: PAGE_SIZE, offset }
      if (state.selectedArtist !== null) params.artist = state.selectedArtist
      if (state.selectedAlbum  !== null) params.album  = state.selectedAlbum
      result = await api.library.tracks(params)
    }
    state.tracks = result.tracks
    state.total  = result.total
    renderTracks()
    renderPagination()
    renderFormatOptions()
  } catch (e) {
    toast(`Failed to load tracks: ${e}`, 'error')
    trackLoading.hidden = true
  }
}

// ─── Scan ─────────────────────────────────────────────────────────────────────

function updateRescanBtn() {
  rescanFolderBtn.disabled = state.selectedDirectory === null || state.scanPollTimer !== null
}

async function startScan(directory?: string) {
  try {
    scanBtn.disabled = true
    rescanFolderBtn.disabled = true
    const { job_id } = await api.jobs.startScan(directory)
    pollScan(job_id)
  } catch (e) {
    const msg = String(e).includes('409') ? 'Another job is already running' : `Scan failed to start: ${e}`
    toast(msg, 'error')
    scanBtn.disabled = false
    updateRescanBtn()
  }
}

function pollScan(jobId: string) {
  if (state.scanPollTimer) clearInterval(state.scanPollTimer)
  state.scanPollTimer = setInterval(async () => {
    try {
      const job = await api.jobs.get(jobId)
      state.scanJob = job
      renderScanStatus()
      if (job.status === 'done' || job.status === 'error') {
        clearInterval(state.scanPollTimer!)
        state.scanPollTimer = null
        scanBtn.disabled = false
        updateRescanBtn()
        await onJobFinished(job)
        renderScanStatus()
      }
    } catch (e) {
      clearInterval(state.scanPollTimer!)
      state.scanPollTimer = null
      scanBtn.disabled = false
      updateRescanBtn()
      toast(`Job polling failed: ${e}`, 'error')
    }
  }, 1000)
}

async function onJobFinished(job: ScanJob) {
  const plural = (n: number) => `${n.toLocaleString()} track${n !== 1 ? 's' : ''}`
  if (job.status === 'error') {
    toast(`${{ scan: 'Scan', unify: 'Album unify', undo: 'Undo', retag: 'Retag', fetch: 'Fetch' }[job.kind] ?? 'Job'} failed: ${job.error}`, 'error')
  } else if (job.kind === 'scan') {
    toast(`Scan complete — ${job.scanned} tracks indexed`, 'success')
  } else if (job.kind === 'unify' || job.kind === 'retag') {
    const verb = job.kind === 'unify' ? 'Unified' : 'Retagged'
    toast(`${verb} ${plural(job.scanned)}${job.error ? ` — ${job.error}` : ''}`, job.error ? 'error' : 'success')
  } else {
    toast(`Undone — restored ${plural(job.scanned)}`, 'success')
  }
  state.qualityIssues = null
  await loadLibrary()
  await loadGenres()
  if (job.kind === 'scan') await loadTree()
  if (state.sidebarMode === 'artists') { await loadArtists(); await loadArtistDetail() }
  await loadTracks()
  if (state.sidebarMode === 'quality') await renderQualityPanel()
  renderEditor()
  await refreshUndoButton()
}

// Pick up a job still running from before a page reload.
async function resumeRunningJob() {
  try {
    const running = (await api.jobs.list())
      .find(j => (j.status === 'pending' || j.status === 'running') && j.kind !== 'fetch')
    if (running) { scanBtn.disabled = true; pollScan(running.id) }
  } catch { /* not critical */ }
}

// The genre fetch job runs alongside others; its progress lives in the Artists panel.
async function resumeFetchAll() {
  if (fetchAllTimer) return
  try {
    const running = (await api.jobs.list())
      .find(j => (j.status === 'pending' || j.status === 'running') && j.kind === 'fetch')
    if (running) pollFetchAll(running.id)
  } catch { /* not critical */ }
}

// ─── Album unify ──────────────────────────────────────────────────────────────

const UNIFY_GROUPS: { label: string; fields: UnifyField[] }[] = [
  { label: 'Genre', fields: ['genre'] },
  { label: 'Year (earliest)', fields: ['year'] },
  { label: 'Album / Album artist / Compilation', fields: ['album', 'album_artist', 'compilation'] },
]

async function showUnifyAlbums() {
  const overlay = document.createElement('div')
  overlay.className = 'modal-overlay'
  overlay.innerHTML = `
    <div class="modal-card unify-card">
      <div class="modal-title">Unify album tags</div>
      <div class="modal-hint" id="unify-summary">Loading…</div>
      <div class="unify-toolbar">
        ${UNIFY_GROUPS.map((g, i) => `<label class="unify-field"><input type="checkbox" data-group="${i}" checked /> ${g.label}</label>`).join('')}
      </div>
      <div class="unify-toolbar">
        <input id="unify-filter" class="nav-filter" type="search" placeholder="Filter albums…" autocomplete="off" />
        <button type="button" class="btn btn-ghost btn-sm" id="unify-all">All</button>
        <button type="button" class="btn btn-ghost btn-sm" id="unify-none">None</button>
      </div>
      <ul class="unify-list" id="unify-list"></ul>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" id="unify-cancel">Cancel</button>
        <button type="button" class="btn btn-primary" id="unify-apply" disabled>Apply</button>
      </div>
    </div>
  `
  document.body.appendChild(overlay)
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey, true) }
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); close() } }
  document.addEventListener('keydown', onKey, true)
  overlay.addEventListener('click', e => { if (e.target === overlay) close() })
  overlay.querySelector('#unify-cancel')!.addEventListener('click', close)

  const listEl    = overlay.querySelector<HTMLElement>('#unify-list')!
  const summaryEl = overlay.querySelector<HTMLElement>('#unify-summary')!
  const applyBtn  = overlay.querySelector<HTMLButtonElement>('#unify-apply')!
  const filterEl  = overlay.querySelector<HTMLInputElement>('#unify-filter')!

  let albums: AlbumInconsistency[]
  try {
    albums = await api.library.albumInconsistencies()
  } catch (e) {
    summaryEl.textContent = `Failed to load: ${e}`
    return
  }

  const enabledFields = (): UnifyField[] =>
    [...overlay.querySelectorAll<HTMLInputElement>('.unify-field input')]
      .filter(cb => cb.checked)
      .flatMap(cb => UNIFY_GROUPS[Number(cb.dataset.group)].fields)

  const chip = (field: UnifyField, change: { before: Record<string, number>; after: string }) => {
    const before = Object.entries(change.before)
      .map(([v, n]) => `<span class="unify-before">${esc(v || '(empty)')} <b>×${n}</b></span>`).join('')
    return `<div class="unify-change" data-field="${field}"><span class="unify-name">${field.replace('_', ' ')}</span> ${before} <span class="unify-arrow">→</span> <span class="unify-after">${esc(change.after || '(empty)')}</span></div>`
  }

  const frag = document.createDocumentFragment()
  albums.forEach((a, i) => {
    const li = document.createElement('li')
    li.className = 'unify-album'
    li.dataset.index = String(i)
    li.dataset.search = `${a.artist ?? ''} ${a.album ?? ''} ${a.directory}`.toLowerCase()
    li.innerHTML = `
      <label class="unify-album-head">
        <input type="checkbox" checked />
        <span class="unify-album-name">${esc(a.artist || '?')} — ${esc(a.album || a.directory.split('/').pop() || '')}</span>
        <span class="nav-count">${a.track_count} tracks</span>
      </label>
      ${(Object.entries(a.changes) as [UnifyField, NonNullable<AlbumInconsistency['changes'][UnifyField]>][])
        .map(([f, c]) => chip(f, c)).join('')}
    `
    frag.appendChild(li)
  })
  listEl.appendChild(frag)

  // Show only albums with a change in an enabled field that match the filter.
  const refresh = () => {
    const fields = new Set(enabledFields())
    const needle = filterEl.value.trim().toLowerCase()
    let shown = 0, checked = 0, tracks = 0
    listEl.querySelectorAll<HTMLElement>('.unify-album').forEach(li => {
      const a = albums[Number(li.dataset.index)]
      li.querySelectorAll<HTMLElement>('.unify-change').forEach(c => { c.hidden = !fields.has(c.dataset.field as UnifyField) })
      const relevant = (Object.keys(a.changes) as UnifyField[]).filter(f => fields.has(f))
      li.hidden = !relevant.length || (!!needle && !li.dataset.search!.includes(needle))
      if (li.hidden) return
      shown++
      if (li.querySelector<HTMLInputElement>('input')!.checked) {
        checked++
        tracks += Math.max(...relevant.map(f => a.changes[f]!.changed))
      }
    })
    summaryEl.textContent = albums.length
      ? `${shown.toLocaleString()} album${shown !== 1 ? 's' : ''} to unify · ${checked.toLocaleString()} selected · at least ${tracks.toLocaleString()} track${tracks !== 1 ? 's' : ''} rewritten`
      : 'Every album is consistent.'
    applyBtn.disabled = checked === 0
    applyBtn.textContent = checked ? `Apply to ${checked.toLocaleString()} album${checked !== 1 ? 's' : ''}` : 'Apply'
  }
  refresh()

  overlay.querySelectorAll('.unify-field input').forEach(cb => cb.addEventListener('change', refresh))
  filterEl.addEventListener('input', refresh)
  listEl.addEventListener('change', refresh)
  const setVisible = (on: boolean) => {
    listEl.querySelectorAll<HTMLElement>('.unify-album:not([hidden]) input').forEach(cb => { (cb as HTMLInputElement).checked = on })
    refresh()
  }
  overlay.querySelector('#unify-all')!.addEventListener('click', () => setVisible(true))
  overlay.querySelector('#unify-none')!.addEventListener('click', () => setVisible(false))

  applyBtn.addEventListener('click', async () => {
    const directories = [...listEl.querySelectorAll<HTMLElement>('.unify-album:not([hidden])')]
      .filter(li => li.querySelector<HTMLInputElement>('input')!.checked)
      .map(li => albums[Number(li.dataset.index)].directory)
    applyBtn.disabled = true
    try {
      const { job_id } = await api.tags.unifyAlbums(directories, enabledFields())
      close()
      scanBtn.disabled = true
      pollScan(job_id)
    } catch (e) {
      toast(String(e).includes('409') ? 'Another job is already running' : `Unify failed: ${e}`, 'error')
      applyBtn.disabled = false
    }
  })
}

// ─── Tag saving ───────────────────────────────────────────────────────────────

async function saveTags(e: Event) {
  e.preventDefault()
  const update: Record<string, string> = {}
  for (const field of TAG_FIELDS) {
    const el = tagForm.elements.namedItem(field) as HTMLInputElement | HTMLTextAreaElement | null
    if (!el) continue
    if ((el as HTMLElement).dataset.mixed === '1' && el.value === '') continue
    update[field] = el.value
  }
  // compilation: apply unless it's still "mixed" (indeterminate) across a multi-selection.
  const cb = compilationCheckbox()
  if (!cb.indeterminate) update.compilation = cb.checked ? '1' : ''
  // Attach MB IDs from pending lookup result
  if (state.pendingLookupResult) {
    const r = state.pendingLookupResult
    if (r.mb_track_id)        update.mb_track_id        = r.mb_track_id
    if (r.mb_artist_id)       update.mb_artist_id       = r.mb_artist_id
    if (r.mb_album_id)        update.mb_album_id        = r.mb_album_id
    if (r.mb_album_artist_id) update.mb_album_artist_id = r.mb_album_artist_id
  }

  // Multi-selection "Add"/"Remove" genre modes edit each track's own genre list.
  const ids = [...state.selectedIds]
  const genreOps: GenreOps = {}
  if (ids.length > 1 && genreModeEl.value !== 'replace') {
    const names = splitGenres(update.genre ?? '')
    delete update.genre
    if (names.length) genreOps[genreModeEl.value === 'add' ? 'genre_add' : 'genre_remove'] = names
  }

  if (Object.keys(update).length === 0 && !Object.keys(genreOps).length) {
    toast('No changes to save', 'info'); return
  }

  try {
    if (ids.length === 1) {
      await api.tags.update(ids[0], update)
    } else {
      await api.tags.bulk(ids, update, genreOps)
    }

    // Write cover art from Cover Art Archive if one was selected via lookup
    if (state.pendingCoverAlbumId) {
      const albumId = state.pendingCoverAlbumId
      try {
        await Promise.all(ids.map(id => api.lookup.applyCover(id, albumId)))
        state.pendingCoverAlbumId = null
        state.pendingLookupResult = null
        state.coverBust++
        updateCoverPreview()
        if (state.viewMode === 'albums') renderAlbumGrid()
        toast('Tags and cover art saved', 'success')
      } catch {
        toast('Tags saved — cover art write failed', 'error')
      }
    } else {
      state.pendingLookupResult = null
      toast('Tags saved', 'success')
    }

    state.qualityIssues = null
    await reloadNav()
    await loadTracks()
    const updated = state.tracks.filter(t => state.selectedIds.has(t.id))
    if (updated.length) populateForm(updated)
    await refreshUndoButton()
  } catch (e) {
    toast(`Save failed: ${e}`, 'error')
  }
}

// ─── Normalize case ───────────────────────────────────────────────────────────

async function normalizeCaseBulk() {
  const selected = state.tracks.filter(t => state.selectedIds.has(t.id))
  if (!selected.length) return

  const tasks: Array<Promise<unknown>> = []
  let changeCount = 0

  for (const track of selected) {
    const upd: Record<string, string> = {}
    for (const field of NORMALIZE_FIELDS) {
      const val = track[field] as string | null
      if (needsNormalization(val)) upd[field as string] = toTitleCase(val!)
    }
    if (Object.keys(upd).length) {
      changeCount++
      tasks.push(api.tags.update(track.id, upd))
    }
  }

  if (!tasks.length) { toast('No tags needed normalization', 'info'); return }

  normalizeCaseBtn.disabled = true
  try {
    await Promise.all(tasks)
    toast(`Normalized case on ${changeCount} track${changeCount !== 1 ? 's' : ''}`, 'success')
    state.qualityIssues = null
    await reloadNav()
    await loadTracks()
    if (state.sidebarMode === 'quality') await renderQualityPanel()
    const updated = state.tracks.filter(t => state.selectedIds.has(t.id))
    if (updated.length) populateForm(updated)
    await refreshUndoButton()
  } catch (e) {
    toast(`Normalize failed: ${e}`, 'error')
  } finally {
    normalizeCaseBtn.disabled = false
  }
}

// ─── Remove dead tracks ───────────────────────────────────────────────────────

async function removeFromLibrary() {
  const ids = [...state.selectedIds]
  if (!ids.length) return
  removeTracksBtn.disabled = true
  try {
    const { removed } = await api.library.removeTracks(ids)
    toast(`Removed ${removed} track${removed !== 1 ? 's' : ''} from library`, 'success')
    state.selectedIds.clear()
    state.qualityIssues = null
    await loadTracks()
    renderEditor()
    await renderQualityPanel()
    await refreshUndoButton()
  } catch (e) {
    toast(`Failed to remove tracks: ${e}`, 'error')
  } finally {
    removeTracksBtn.disabled = false
  }
}

// ─── Delete files / reorganize ────────────────────────────────────────────────────

async function deleteFiles() {
  const ids = [...state.selectedIds]
  if (!ids.length) return
  const ok = await confirmModal(
    'Delete files',
    `Move ${ids.length} file${ids.length !== 1 ? 's' : ''} to trash? ` +
    `The files leave your library folder but can be restored with Undo.`,
    'Move to trash',
  )
  if (!ok) return
  deleteFilesBtn.disabled = true
  try {
    const { deleted } = await api.library.deleteFiles(ids)
    toast(`Moved ${deleted} file${deleted !== 1 ? 's' : ''} to trash`, 'success')
    state.selectedIds.clear()
    state.qualityIssues = null
    await loadTracks()
    renderEditor()
    if (state.sidebarMode === 'quality') await renderQualityPanel()
    await refreshUndoButton()
  } catch (e) {
    toast(`Delete failed: ${e}`, 'error')
  } finally {
    deleteFilesBtn.disabled = false
  }
}

async function organizeFiles() {
  const ids = [...state.selectedIds]
  if (!ids.length) return
  organizeBtn.disabled = true
  try {
    const { moved, errors } = await api.tags.reorganize(ids)
    if (moved === 0 && errors.length === 0) {
      toast('Files already match the template', 'info')
    } else {
      const errNote = errors.length ? `, ${errors.length} skipped` : ''
      toast(`Moved ${moved} file${moved !== 1 ? 's' : ''}${errNote}`, moved ? 'success' : 'error')
    }
    await reloadNav()
    if (state.sidebarMode === 'files') await loadTree()
    await loadTracks()
    renderEditor()
    await refreshUndoButton()
  } catch (e) {
    toast(`Organize failed: ${e}`, 'error')
  } finally {
    organizeBtn.disabled = false
  }
}

// ─── Album flows: auto-number & find/replace ────────────────────────────────────

async function refreshAfterBulk() {
  state.qualityIssues = null
  await reloadNav()
  await loadTracks()
  if (state.sidebarMode === 'quality') await renderQualityPanel()
  const updated = state.tracks.filter(t => state.selectedIds.has(t.id))
  if (updated.length) populateForm(updated)
  await refreshUndoButton()
}

async function autoNumber() {
  const ids = [...state.selectedIds]
  if (!ids.length) return
  autonumberBtn.disabled = true
  try {
    const { numbered } = await api.tags.autonumber(ids)
    toast(`Numbered ${numbered} track${numbered !== 1 ? 's' : ''}`, 'success')
    await refreshAfterBulk()
  } catch (e) {
    toast(`Auto-number failed: ${e}`, 'error')
  } finally {
    autonumberBtn.disabled = false
  }
}

const FIND_REPLACE_FIELDS: { key: string; label: string }[] = [
  { key: 'title', label: 'Title' }, { key: 'artist', label: 'Artist' },
  { key: 'album', label: 'Album' }, { key: 'album_artist', label: 'Album Artist' },
  { key: 'genre', label: 'Genre' }, { key: 'composer', label: 'Composer' },
  { key: 'comment', label: 'Comment' },
]

function showFindReplace() {
  const ids = [...state.selectedIds]
  if (!ids.length) return
  const overlay = document.createElement('div')
  overlay.className = 'modal-overlay'
  overlay.innerHTML = `
    <form class="modal-card" id="fr-form">
      <div class="modal-title">Find & replace in ${ids.length} track${ids.length !== 1 ? 's' : ''}</div>
      <label class="field-label">Field
        <select id="fr-field">${FIND_REPLACE_FIELDS.map(f => `<option value="${f.key}">${f.label}</option>`).join('')}</select>
      </label>
      <label class="field-label">Find<input id="fr-find" type="text" autocomplete="off" /></label>
      <label class="field-label">Replace with<input id="fr-replace" type="text" autocomplete="off" /></label>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" id="fr-cancel">Cancel</button>
        <button type="submit" class="btn btn-primary">Replace</button>
      </div>
    </form>
  `
  document.body.appendChild(overlay)
  const close = () => overlay.remove()
  overlay.addEventListener('click', e => { if (e.target === overlay) close() })
  overlay.querySelector('#fr-cancel')!.addEventListener('click', close)
  const findInput = overlay.querySelector<HTMLInputElement>('#fr-find')!
  findInput.focus()
  overlay.querySelector<HTMLFormElement>('#fr-form')!.addEventListener('submit', async (e) => {
    e.preventDefault()
    const field = overlay.querySelector<HTMLSelectElement>('#fr-field')!.value
    const find = findInput.value
    const replace = overlay.querySelector<HTMLInputElement>('#fr-replace')!.value
    if (!find) { findInput.focus(); return }
    try {
      const { changed } = await api.tags.findReplace(ids, field, find, replace)
      toast(changed ? `Replaced in ${changed} track${changed !== 1 ? 's' : ''}` : 'No matches found',
            changed ? 'success' : 'info')
      close()
      await refreshAfterBulk()
    } catch (err) {
      toast(`Find/replace failed: ${err}`, 'error')
    }
  })
}

// ─── Infer tags from filename ───────────────────────────────────────────────────

async function inferFromFilename() {
  if (state.selectedIds.size !== 1) return
  const trackId = [...state.selectedIds][0]
  inferBtn.disabled = true
  inferBtn.textContent = 'Reading…'
  try {
    const result = await api.lookup.infer(trackId)
    if (!result) { toast('Could not infer anything from the file name', 'info'); return }
    applyLookupResult(result)
  } catch (e) {
    toast(`Inference failed: ${e}`, 'error')
  } finally {
    inferBtn.disabled = false
    inferBtn.textContent = 'From filename'
  }
}

// ─── Export current view as M3U ─────────────────────────────────────────────────

function currentViewParams(): Record<string, string | number> {
  if (state.query) return { q: state.query }
  if (state.sidebarMode === 'files') {
    return state.selectedDirectory !== null ? { directory: state.selectedDirectory } : {}
  }
  if (state.sidebarMode === 'genres') {
    return state.selectedGenre !== null ? { genre: state.selectedGenre } : {}
  }
  if (state.sidebarMode === 'artists') {
    return state.selectedArtistKey !== null ? { artist_key: state.selectedArtistKey } : {}
  }
  if (state.sidebarMode === 'quality') {
    return state.selectedIssue && state.selectedIssue !== 'missing_files'
      ? { issue: state.selectedIssue }
      : {}
  }
  const params: Record<string, string | number> = {}
  if (state.selectedArtist !== null) params.artist = state.selectedArtist
  if (state.selectedAlbum !== null) params.album = state.selectedAlbum
  return params
}

function exportM3u() {
  if (!state.total) { toast('Nothing to export', 'info'); return }
  const url = api.library.exportM3uUrl(currentViewParams())
  const a = document.createElement('a')
  a.href = url
  a.download = 'tagger-export.m3u'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// ─── ReplayGain scan ────────────────────────────────────────────────────────────

async function scanReplayGain() {
  const ids = [...state.selectedIds]
  if (!ids.length) return
  replaygainBtn.disabled = true
  replaygainBtn.textContent = 'Scanning…'
  try {
    const res = await api.tags.replaygain(ids, ids.length > 1)
    if (res.ok) {
      toast(`ReplayGain written to ${res.processed} track${res.processed !== 1 ? 's' : ''} (${res.tool})`, 'success')
    } else {
      toast(`ReplayGain failed: ${res.error ?? 'unknown error'}`, 'error')
    }
  } catch (e) {
    toast(`ReplayGain failed: ${e}`, 'error')
  } finally {
    replaygainBtn.disabled = false
    replaygainBtn.textContent = 'ReplayGain'
  }
}

// ─── Undo / history ─────────────────────────────────────────────────────────────

async function refreshUndoButton() {
  try {
    const history = await api.library.history(10)
    const undoable = history.find(h => !h.undone)
    undoBtn.hidden = !undoable
    undoBtn.title = undoable ? `Undo: ${undoable.summary}` : 'Nothing to undo'
    undoBtn.dataset.changeId = undoable ? String(undoable.id) : ''
  } catch {
    undoBtn.hidden = true
  }
}

async function undoLast() {
  const id = undoBtn.dataset.changeId
  if (!id) return
  undoBtn.disabled = true
  try {
    const res = await api.library.undo(Number(id))
    if (res.job_id) { scanBtn.disabled = true; pollScan(res.job_id); return }
    toast(`Undone — restored ${res.restored} track${res.restored !== 1 ? 's' : ''}`, 'success')
    state.qualityIssues = null
    await reloadNav()
    await loadTracks()
    if (state.sidebarMode === 'quality') await renderQualityPanel()
    renderEditor()
  } catch (e) {
    toast(`Undo failed: ${e}`, 'error')
  } finally {
    undoBtn.disabled = false
    await refreshUndoButton()
  }
}

// ─── Event handlers ───────────────────────────────────────────────────────────

function onSelectAll() {
  const sa = selectAll()
  if (!sa) return
  state.tracks.forEach(t => sa.checked ? state.selectedIds.add(t.id) : state.selectedIds.delete(t.id))
  renderTracks()
  renderEditor()
}

// ─── Event wiring ─────────────────────────────────────────────────────────────

document.getElementById('sidebar-toggle')!.addEventListener('click', () => {
  appEl.classList.toggle('sidebar-collapsed')
})

// On phones/tablets the sidebar overlays the content, so start it collapsed
// and close it once the user picks something from it.
const MOBILE_BP = 820
if (window.innerWidth <= MOBILE_BP) appEl.classList.add('sidebar-collapsed')
document.querySelector('.sidebar')!.addEventListener('click', (e) => {
  if (window.innerWidth > MOBILE_BP) return
  if ((e.target as HTMLElement).closest('.nav-item, .tree-row, .quality-issue-item')) {
    appEl.classList.add('sidebar-collapsed')
  }
})

// ─── Resizable panes ──────────────────────────────────────────────────────────
// The left nav and the right tag editor can each be widened by dragging the
// divider beside them; widths persist per-browser and reset on double-click.
const PANE = {
  sidebar: { key: 'tagger_sidebar_w', varName: '--sidebar-w', def: 220, min: 160, max: 560, el: sidebarEl },
  editor:  { key: 'tagger_editor_w',  varName: '--editor-w',  def: 272, min: 240, max: 720, el: tagEditor },
} as const
type PaneName = keyof typeof PANE

const clampPane = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

function applyPaneWidth(name: PaneName, px: number) {
  document.documentElement.style.setProperty(PANE[name].varName, `${Math.round(px)}px`)
}

function restorePaneWidths() {
  for (const name of Object.keys(PANE) as PaneName[]) {
    const p = PANE[name]
    const saved = parseInt(localStorage.getItem(p.key) || '', 10)
    if (!Number.isNaN(saved)) applyPaneWidth(name, clampPane(saved, p.min, p.max))
  }
}

function beginPaneResize(e: PointerEvent) {
  if (window.innerWidth <= MOBILE_BP) return   // panes overlay on mobile; no resize
  const handle = e.currentTarget as HTMLElement
  const name = handle.dataset.resize as PaneName
  const p = PANE[name]
  e.preventDefault()
  const startX = e.clientX
  const startW = p.el.getBoundingClientRect().width
  handle.classList.add('dragging')
  document.body.classList.add('resizing-pane')
  handle.setPointerCapture(e.pointerId)

  const onMove = (ev: PointerEvent) => {
    // Sidebar sits left of its handle (drag right = wider); editor sits right
    // of its handle (drag left = wider).
    const delta = name === 'sidebar' ? ev.clientX - startX : startX - ev.clientX
    applyPaneWidth(name, clampPane(startW + delta, p.min, p.max))
  }
  const onUp = () => {
    handle.releasePointerCapture(e.pointerId)
    handle.removeEventListener('pointermove', onMove)
    handle.removeEventListener('pointerup', onUp)
    handle.classList.remove('dragging')
    document.body.classList.remove('resizing-pane')
    localStorage.setItem(p.key, String(Math.round(p.el.getBoundingClientRect().width)))
  }
  handle.addEventListener('pointermove', onMove)
  handle.addEventListener('pointerup', onUp)
}

for (const handle of document.querySelectorAll<HTMLElement>('.pane-resizer')) {
  handle.addEventListener('pointerdown', beginPaneResize)
  handle.addEventListener('dblclick', () => {
    const name = handle.dataset.resize as PaneName
    applyPaneWidth(name, PANE[name].def)
    localStorage.removeItem(PANE[name].key)
  })
}
restorePaneWidths()

// Sidebar tabs
document.querySelector('.sidebar-tabs')!.addEventListener('click', async (e) => {
  const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('.stab')
  if (!btn?.dataset.mode) return
  const mode = btn.dataset.mode as SidebarMode
  if (mode === state.sidebarMode) return
  state.sidebarMode = mode
  if (mode === 'tags') {
    state.selectedDirectory = null
    if (!state.artists.length) await loadLibrary()
  } else if (mode === 'genres') {
    await loadGenres()
  } else if (mode === 'artists') {
    renderSidebarTabs()
    await loadArtists()
    loadArtistDetail()
    resumeFetchAll()
  } else if (mode === 'files') {
    state.selectedArtist = null
    state.selectedAlbum  = null
    if (!state.rootNode) await loadTree()
  } else {
    state.selectedIssue = null
    qualityToolbar.hidden = true
    renderSidebarTabs()
    await renderQualityPanel()
    state.selectedIds.clear()
    state.tracks = []; state.total = 0
    renderTracks()
    renderEditor()
    return
  }
  state.selectedIds.clear()
  state.page = 0
  renderSidebarTabs()
  await loadTracks()
  renderEditor()
})

// Tags panel: artist/album clicks
artistListEl.addEventListener('click', async (e) => {
  const li = (e.target as HTMLElement).closest<HTMLElement>('.nav-item')
  if (!li) return
  clearSearch()

  const artist = li.dataset.artist ?? null
  const album  = li.dataset.album  ?? null

  // "All tracks"
  if (li.dataset.all === '1') {
    state.selectedArtist  = null
    state.selectedAlbum   = null
    state.expandedArtists.clear()
    state.selectedIds.clear()
    state.page = 0
    renderTagsPanel()
    await loadTracks()
    renderEditor()
    return
  }

  // Album click (has both artist and album dataset attrs)
  if (album !== null) {
    state.selectedArtist = artist
    state.selectedAlbum  = album
    state.selectedIds.clear()
    state.page = 0
    renderTagsPanel()
    await loadTracks()
    renderEditor()
    return
  }

  // Artist row click — select artist and toggle expand/collapse
  if (state.expandedArtists.has(artist!)) {
    state.expandedArtists.delete(artist!)
  } else {
    state.expandedArtists.add(artist!)
  }
  state.selectedArtist = artist
  state.selectedAlbum  = null
  state.selectedIds.clear()
  state.page = 0
  renderTagsPanel()
  await loadTracks()
  renderEditor()
})

// Tags panel: expand/collapse all
document.getElementById('expand-all-btn')!.addEventListener('click', () => {
  for (const a of state.artists) state.expandedArtists.add(a.artist)
  renderTagsPanel()
})

document.getElementById('collapse-all-btn')!.addEventListener('click', () => {
  state.expandedArtists.clear()
  renderTagsPanel()
})

// Artists panel: artist clicks, filter, fetch all; genre panel actions
artistKeyListEl.addEventListener('click', async (e) => {
  const li = (e.target as HTMLElement).closest<HTMLElement>('.nav-item')
  if (!li) return
  clearSearch()
  state.selectedArtistKey = li.dataset.all === '1' ? null : (li.dataset.artistKey ?? null)
  state.selectedIds.clear()
  state.page = 0
  renderArtistsPanel()
  loadArtistDetail()
  await loadTracks()
  renderEditor()
})

artistFilterEl.addEventListener('input', () => {
  state.artistFilter = artistFilterEl.value
  renderArtistsPanel()
})

fetchAllBtn.addEventListener('click', async () => {
  try {
    const { job_id } = await api.artists.fetchAll()
    pollFetchAll(job_id)
  } catch (e) {
    toast(String(e).includes('409') ? 'Genres are already being fetched' : `Fetch failed: ${e}`, 'error')
  }
})

artistGenresEl.addEventListener('click', (e) => {
  const action = (e.target as HTMLElement).closest<HTMLElement>('[data-ag]')?.dataset.ag
  if (action === 'fetch') fetchArtistGenres()
  else if (action === 'replace' || action === 'add') retagArtist(action)
})
artistGenresEl.addEventListener('change', (e) => {
  const target = e.target as HTMLElement
  if (target.matches('.ag-candidates')) fetchArtistGenres((target as HTMLSelectElement).value)
  else updatePickedCount()
})

// Genres panel: genre clicks, rename/remove actions, filter
genreListEl.addEventListener('click', async (e) => {
  const target = e.target as HTMLElement
  const li = target.closest<HTMLElement>('.nav-item')
  if (!li) return

  const action = target.closest<HTMLElement>('.nav-action')?.dataset.action
  if (action && li.dataset.genre) {
    if (action === 'rename') showRenameGenre(li.dataset.genre)
    else await deleteGenre(li.dataset.genre)
    return
  }

  clearSearch()
  state.selectedGenre = li.dataset.all === '1' ? null : (li.dataset.genre ?? null)
  state.selectedIds.clear()
  state.page = 0
  renderGenresPanel()
  await loadTracks()
  renderEditor()
})

genreFilterEl.addEventListener('input', () => {
  state.genreFilter = genreFilterEl.value
  renderGenresPanel()
})

// Files panel: directory tree clicks
dirTreeEl.addEventListener('click', async (e) => {
  const target = e.target as HTMLElement
  const row = target.closest<HTMLElement>('.tree-row')
  if (!row) return
  const path = row.dataset.path ?? null

  if (target.dataset.toggle === '1') {
    const node = findDirNode(path)
    if (node) { await expandDirNode(node); return }
  }

  clearSearch()
  state.selectedDirectory = path === '' ? null : path
  state.selectedIds.clear()
  state.page = 0
  renderFilesPanel()
  await loadTracks()
  renderEditor()
})

function findDirNode(path: string | null): DirNode | null {
  if (!state.rootNode || path === null) return null
  function search(node: DirNode): DirNode | null {
    if (node.path === path) return node
    if (node.children) for (const c of node.children) { const r = search(c); if (r) return r }
    return null
  }
  return search(state.rootNode)
}

// Tag-link navigation
async function navigateTo(artist: string, album?: string) {
  clearSearch()
  state.sidebarMode     = 'tags'
  state.selectedArtist  = artist || null
  state.selectedAlbum   = album  || null
  state.selectedIds.clear()
  state.page = 0
  if (artist) state.expandedArtists.add(artist)
  renderSidebarTabs()
  renderTagsPanel()
  await loadTracks()
  renderEditor()
}

// Track table clicks
trackTbody.addEventListener('click', (e) => {
  const tr = (e.target as HTMLElement).closest<HTMLTableRowElement>('tr')
  if (!tr) return
  const id = parseInt(tr.dataset.id!, 10)
  const isCheckbox = (e.target as HTMLElement).matches('input[type=checkbox]')
  const cb = tr.querySelector<HTMLInputElement>('input[type=checkbox]')!

  if (e.shiftKey && state.selectedIds.size > 0) {
    const rows  = [...trackTbody.querySelectorAll<HTMLTableRowElement>('tr')]
    const ids   = rows.map(r => parseInt(r.dataset.id!, 10))
    const idArr = [...state.selectedIds]
    const last  = idArr[idArr.length - 1]
    const from = ids.indexOf(last), to = ids.indexOf(id)
    const [lo, hi] = from < to ? [from, to] : [to, from]
    ids.slice(lo, hi + 1).forEach(i => state.selectedIds.add(i))
  } else if (isCheckbox) {
    cb.checked ? state.selectedIds.add(id) : state.selectedIds.delete(id)
  } else {
    if (state.selectedIds.has(id) && state.selectedIds.size === 1) {
      state.selectedIds.clear()
    } else {
      state.selectedIds.clear()
      state.selectedIds.add(id)
    }
  }
  renderTracks()
  renderEditor()
})

// MusicBrainz lookup
lookupBtn.addEventListener('click', runLookup)
spectroBtn.addEventListener('click', toggleSpectro)
spectroPopout.addEventListener('click', openSpectroLightbox)
spectroImg.addEventListener('click', openSpectroLightbox)
document.getElementById('lookup-close')!.addEventListener('click', () => { lookupPanel.hidden = true })

// View toggle
viewListBtn.addEventListener('click', () => {
  if (state.viewMode === 'list') return
  state.viewMode = 'list'
  renderViewMode()
})

viewAlbumsBtn.addEventListener('click', () => {
  if (state.viewMode === 'albums') return
  state.viewMode = 'albums'
  renderViewMode()
})

// Cover art upload
coverInput.addEventListener('change', async () => {
  const file = coverInput.files?.[0]
  if (!file) return
  const ids = [...state.selectedIds]
  if (!ids.length) return
  try {
    await Promise.all(ids.map(id => api.covers.update(id, file)))
    state.coverBust++
    updateCoverPreview()
    if (state.viewMode === 'albums') renderAlbumGrid()
    toast(`Cover art updated`, 'success')
  } catch (err) {
    toast(`Failed to update cover: ${err}`, 'error')
  }
  coverInput.value = ''
})

// Column picker
colPickerBtn.addEventListener('click', (e) => {
  e.stopPropagation()
  state.colPickerOpen = !state.colPickerOpen
  colPickerEl.hidden  = !state.colPickerOpen
  if (state.colPickerOpen) renderColPicker()
})

colPickerEl.addEventListener('change', (e) => {
  const cb = e.target as HTMLInputElement
  if (!cb.dataset.col) return
  if (cb.checked) state.visibleCols.add(cb.dataset.col)
  else            state.visibleCols.delete(cb.dataset.col)
  saveColPrefs(state.visibleCols)
  renderColHeaders()
  renderTracks()
})

document.addEventListener('click', (e) => {
  if (state.colPickerOpen && !colPickerEl.contains(e.target as Node) && e.target !== colPickerBtn) {
    state.colPickerOpen = false
    colPickerEl.hidden  = true
  }
})

document.getElementById('clear-selection')!.addEventListener('click', () => {
  state.selectedIds.clear(); renderTracks(); renderEditor()
})

normalizeCaseBtn.addEventListener('click', normalizeCaseBulk)
removeTracksBtn.addEventListener('click', removeFromLibrary)
deleteFilesBtn.addEventListener('click', deleteFiles)
organizeBtn.addEventListener('click', organizeFiles)
autonumberBtn.addEventListener('click', autoNumber)
findReplaceBtn.addEventListener('click', showFindReplace)
replaygainBtn.addEventListener('click', scanReplayGain)
inferBtn.addEventListener('click', inferFromFilename)
exportM3uBtn.addEventListener('click', exportM3u)
selectMatchingBtn.addEventListener('click', selectAllMatching)
genreModeEl.addEventListener('change', onGenreModeChange)
undoBtn.addEventListener('click', undoLast)

// Filter selects
filterQualityEl.addEventListener('change', () => { state.filterQuality = filterQualityEl.value; renderTracks() })
filterFormatEl.addEventListener('change',  () => { state.filterFormat  = filterFormatEl.value;  renderTracks() })


// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  const target = e.target as HTMLElement
  if (target.matches('input, textarea, select') || target.isContentEditable) return

  if (e.key === 'Escape') {
    if (!tagEditor.hidden) {
      state.selectedIds.clear()
      renderTracks()
      renderEditor()
    }
  } else if (e.key === 'ArrowUp') {
    if (state.selectedIds.size > 0) {
      e.preventDefault()
      navigateTrack(-1)
    }
  } else if (e.key === 'ArrowDown') {
    if (state.selectedIds.size > 0) {
      e.preventDefault()
      navigateTrack(1)
    }
  } else if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    if (!tagEditor.hidden) {
      e.preventDefault()
      tagForm.requestSubmit()
    }
  } else if (e.key === '?') {
    e.preventDefault()
    showKeyboardHelp()
  }
})

const SHORTCUTS: [string, string][] = [
  ['↑ / ↓', 'Move between tracks'],
  ['Click', 'Select a track'],
  ['Shift-click', 'Select a range'],
  ['Ctrl/⌘ + S', 'Save tags'],
  ['Esc', 'Close the editor'],
  ['?', 'Show this help'],
]

function showKeyboardHelp() {
  if (document.querySelector('.modal-overlay')) return
  const overlay = document.createElement('div')
  overlay.className = 'modal-overlay'
  const rows = SHORTCUTS.map(([k, d]) => `<tr><td class="kbd-key"><kbd>${esc(k)}</kbd></td><td>${esc(d)}</td></tr>`).join('')
  overlay.innerHTML = `
    <div class="modal-card">
      <div class="modal-title">Keyboard shortcuts</div>
      <table class="kbd-table"><tbody>${rows}</tbody></table>
      <div class="modal-actions"><button class="btn btn-primary" id="kbd-close">Close</button></div>
    </div>
  `
  document.body.appendChild(overlay)
  const close = () => overlay.remove()
  overlay.addEventListener('click', e => { if (e.target === overlay) close() })
  overlay.querySelector('#kbd-close')!.addEventListener('click', close)
}

// Quality panel: issue item clicks
qualityListEl.addEventListener('click', async (e) => {
  const li = (e.target as HTMLElement).closest<HTMLElement>('.quality-issue-item')
  if (!li?.dataset.issue) return
  const issue = li.dataset.issue
  if (issue === 'inconsistent_albums') { await showUnifyAlbums(); return }
  clearSearch()
  state.selectedIssue = state.selectedIssue === issue ? null : issue
  qualityToolbar.hidden = !state.selectedIssue || state.selectedIssue === 'missing_files'
  const isDupes = state.selectedIssue === 'duplicate_tracks'
  dedupeBtn.hidden = !isDupes
  fixAllBtn.hidden = isDupes
  state.selectedIds.clear()
  state.page = 0
  await renderQualityPanel()
  await loadTracks()
  renderEditor()
})

document.getElementById('close-editor')!.addEventListener('click', () => {
  state.selectedIds.clear(); renderTracks(); renderEditor()
})

tagForm.addEventListener('submit', saveTags)
tagForm.addEventListener('input', (e) => { delete (e.target as HTMLElement).dataset.mixed; updateEditorRenamePreview() })

document.getElementById('revert-btn')!.addEventListener('click', () => {
  populateForm(state.tracks.filter(t => state.selectedIds.has(t.id)))
})

const debouncedSearch = debounce(async () => {
  state.selectedIds.clear()
  state.page = 0
  await loadTracks()
  renderEditor()
}, 300)

// Picking something in the sidebar means "show me that": a leftover search
// would otherwise keep overriding the list.
function clearSearch() {
  state.query = ''
  searchEl.value = ''
}

searchEl.addEventListener('input', () => {
  state.query = searchEl.value.trim()
  renderArtistGenres()
  debouncedSearch()
})

scanBtn.addEventListener('click', () => startScan())
rescanFolderBtn.addEventListener('click', () => {
  if (state.selectedDirectory) startScan(state.selectedDirectory)
})

// ─── Settings modal ───────────────────────────────────────────────────────────

const settingsModal     = document.getElementById('settings-sidebar')!
const acoustidKeyInput  = document.getElementById('setting-acoustid-key') as HTMLInputElement
const discogsTokenInput = document.getElementById('setting-discogs-token') as HTMLInputElement
const scanExcludeInput  = document.getElementById('setting-scan-exclude') as HTMLTextAreaElement
const genreSeparatorsInput = document.getElementById('setting-genre-separators') as HTMLInputElement
const autoScanInput     = document.getElementById('setting-auto-scan') as HTMLInputElement
const renameOnSaveInput = document.getElementById('setting-rename-on-save') as HTMLInputElement
const renameTemplateInput = document.getElementById('setting-rename-template') as HTMLInputElement
const renamePreviewEl   = document.getElementById('rename-preview')!
const replaygainStatusEl = document.getElementById('replaygain-status')!
const musicDirsDefaultEl  = document.getElementById('music-dirs-default')!
const musicDirsListEl     = document.getElementById('music-dirs-list')!
const musicDirInput       = document.getElementById('music-dir-input') as HTMLInputElement

let localMusicDirs: string[] = []

function renderMusicDirsList() {
  musicDirsListEl.innerHTML = ''
  for (const dir of localMusicDirs) {
    const li = document.createElement('li')
    li.className = 'music-dir-row'
    li.innerHTML = `<span class="music-dir-path">${esc(dir)}</span><button class="btn btn-ghost btn-sm btn-icon music-dir-remove" data-dir="${esc(dir)}" title="Remove">✕</button>`
    musicDirsListEl.appendChild(li)
  }
}
const renameTemplateWrap  = document.getElementById('rename-template-wrap')!
const acoustidStatusEl  = document.getElementById('acoustid-status')!

function getScanTagCheckboxes(): NodeListOf<HTMLInputElement> {
  return document.querySelectorAll<HTMLInputElement>('#scan-tags-list input[data-tag]')
}

async function openSettings() {
  try {
    const [s, status, rgStatus] = await Promise.all([
      api.settings.get(), api.lookup.status(), api.tags.replaygainStatus(),
    ])
    acoustidKeyInput.value    = s.acoustid_api_key
    discogsTokenInput.value   = s.discogs_token ?? ''
    scanExcludeInput.value    = (s.scan_exclude ?? []).join('\n')
    genreSeparatorsInput.value = (s.genre_separators ?? [';']).join('')
    autoScanInput.value       = String(s.auto_scan_minutes ?? 0)
    renameOnSaveInput.checked = s.rename_on_save
    renameTemplateInput.value = s.rename_template
    renameTemplateWrap.style.display = s.rename_on_save ? '' : 'none'
    updateRenamePreview()
    getScanTagCheckboxes().forEach(cb => {
      cb.checked = s.scan_tags.includes(cb.dataset.tag!)
    })
    musicDirsDefaultEl.textContent = s.default_music_dir ?? ''
    localMusicDirs = [...(s.music_dirs ?? [])]
    renderMusicDirsList()
    renderAcoustidStatus(status)
    replaygainStatusEl.innerHTML = rgStatus.available
      ? `<span class="status-ok">✓ ReplayGain available via ${rgStatus.tool}</span>`
      : '<span class="status-warn">⚠ No ReplayGain tool found — install rsgain or loudgain on the server</span>'
  } catch (e) {
    toast(`Failed to load settings: ${e}`, 'error')
  }
  logoutBtn.hidden = !authRequired
  refreshTrashStatus()
  settingsModal.classList.add('open')
}

const trashStatusEl = document.getElementById('trash-status')!
const emptyTrashBtn = document.getElementById('empty-trash-btn') as HTMLButtonElement

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let v = n / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

async function refreshTrashStatus() {
  try {
    const { count, bytes } = await api.library.trashInfo()
    trashStatusEl.textContent = count
      ? `${count} file${count !== 1 ? 's' : ''} in trash · ${fmtBytes(bytes)}`
      : 'Trash is empty'
    emptyTrashBtn.disabled = count === 0
  } catch {
    trashStatusEl.textContent = ''
  }
}

emptyTrashBtn.addEventListener('click', async () => {
  if (!await confirmModal('Empty trash', 'Permanently delete all files in the trash? This cannot be undone.', 'Delete forever')) return
  emptyTrashBtn.disabled = true
  try {
    const { removed, bytes } = await api.library.emptyTrash()
    toast(`Emptied trash — removed ${removed} file${removed !== 1 ? 's' : ''} (${fmtBytes(bytes)})`, 'success')
    await refreshTrashStatus()
  } catch (e) {
    toast(`Failed to empty trash: ${e}`, 'error')
  }
})

const updateRenamePreview = debounce(async () => {
  const tpl = renameTemplateInput.value.trim()
  if (!tpl) { renamePreviewEl.textContent = ''; return }
  try {
    const res = await api.settings.renamePreview(tpl)
    if (res.ok) {
      renamePreviewEl.textContent = 'Preview: ' + res.preview
      renamePreviewEl.classList.remove('rename-preview-error')
    } else {
      renamePreviewEl.textContent = res.error ?? 'Invalid template'
      renamePreviewEl.classList.add('rename-preview-error')
    }
  } catch {
    renamePreviewEl.textContent = ''
  }
}, 250)

function renderAcoustidStatus(status: { acoustid_configured: boolean; fpcalc_available: boolean; method: string }) {
  if (!status.acoustid_configured) {
    acoustidStatusEl.innerHTML = ''
    return
  }
  if (status.fpcalc_available) {
    acoustidStatusEl.innerHTML = '<span class="status-ok">✓ AcoustID active — fingerprint lookup enabled</span>'
  } else {
    acoustidStatusEl.innerHTML = '<span class="status-warn">⚠ API key set but fpcalc not found — install libchromaprint-tools</span>'
  }
}

function closeSettings() {
  settingsModal.classList.remove('open')
}

document.getElementById('settings-btn')!.addEventListener('click', () => {
  settingsModal.classList.contains('open') ? closeSettings() : openSettings()
})
document.getElementById('settings-modal-close')!.addEventListener('click', closeSettings)

const logoutBtn = document.getElementById('settings-logout') as HTMLButtonElement
logoutBtn.addEventListener('click', async () => {
  try {
    await api.auth.logout()
    showLogin()
    closeSettings()
  } catch (e) {
    toast(`Logout failed: ${e}`, 'error')
  }
})

renameOnSaveInput.addEventListener('change', () => {
  renameTemplateWrap.style.display = renameOnSaveInput.checked ? '' : 'none'
})

renameTemplateInput.addEventListener('input', updateRenamePreview)

document.getElementById('music-dir-add-btn')!.addEventListener('click', () => {
  const val = musicDirInput.value.trim()
  if (!val) return
  if (!localMusicDirs.includes(val)) {
    localMusicDirs.push(val)
    renderMusicDirsList()
  }
  musicDirInput.value = ''
})

musicDirInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') document.getElementById('music-dir-add-btn')!.click()
})

musicDirsListEl.addEventListener('click', (e) => {
  const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('.music-dir-remove')
  if (!btn) return
  const dir = btn.dataset.dir!
  localMusicDirs = localMusicDirs.filter(d => d !== dir)
  renderMusicDirsList()
})

document.getElementById('settings-save')!.addEventListener('click', async () => {
  const scanTags: string[] = []
  getScanTagCheckboxes().forEach(cb => { if (cb.checked) scanTags.push(cb.dataset.tag!) })
  const update: Partial<AppSettings> = {
    acoustid_api_key:  acoustidKeyInput.value.trim(),
    discogs_token:     discogsTokenInput.value.trim(),
    rename_on_save:    renameOnSaveInput.checked,
    rename_template:   renameTemplateInput.value.trim(),
    scan_tags:         scanTags,
    music_dirs:        localMusicDirs,
    scan_exclude:      scanExcludeInput.value.split('\n').map(x => x.trim()).filter(Boolean),
    auto_scan_minutes: Math.max(0, parseInt(autoScanInput.value, 10) || 0),
    genre_separators:  [...new Set(genreSeparatorsInput.value.replace(/\s+/g, ''))],
  }
  try {
    const saved = await api.settings.update(update)
    state.musicDirs = [saved.default_music_dir ?? '', ...(saved.music_dirs ?? [])].filter(Boolean)
    renameOnSave = saved.rename_on_save
    renameTemplate = saved.rename_template
    // Refresh lookup status after saving
    const status = await api.lookup.status()
    lookupBtn.title = status.method === 'acoustid'
      ? 'Identify via AcoustID fingerprint'
      : 'Search MusicBrainz by title/artist/album'
    toast('Settings saved', 'success')
    closeSettings()
  } catch (e) {
    toast(`Failed to save settings: ${e}`, 'error')
  }
})

// ─── Authentication gate ────────────────────────────────────────────────────────

let authRequired = false
let loginOverlay: HTMLElement | null = null

function showLogin() {
  if (loginOverlay) { loginOverlay.hidden = false; return }
  const overlay = document.createElement('div')
  overlay.className = 'login-overlay'
  overlay.innerHTML = `
    <form class="login-card" id="login-form">
      <div class="login-title">Tagger</div>
      <p class="login-hint">This library is password-protected.</p>
      <input id="login-password" type="password" placeholder="Password" autocomplete="current-password" autofocus />
      <div id="login-error" class="login-error"></div>
      <button type="submit" class="btn btn-primary">Log in</button>
    </form>
  `
  document.body.appendChild(overlay)
  loginOverlay = overlay
  const form = overlay.querySelector<HTMLFormElement>('#login-form')!
  const pw = overlay.querySelector<HTMLInputElement>('#login-password')!
  const err = overlay.querySelector<HTMLElement>('#login-error')!
  form.addEventListener('submit', async (e) => {
    e.preventDefault()
    err.textContent = ''
    try {
      await api.auth.login(pw.value)
      overlay.hidden = true
      pw.value = ''
      await startApp()
    } catch {
      err.textContent = 'Incorrect password'
      pw.select()
    }
  })
  pw.focus()
}

// ─── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  renderColHeaders()
  renderViewMode()
  setUnauthorizedHandler(showLogin)
  try {
    const st = await api.auth.status()
    authRequired = st.required
    if (st.required && !st.authed) { showLogin(); return }
  } catch { /* status unreachable — fall through and let calls surface errors */ }
  await startApp()
}

async function startApp() {
  api.lookup.status().then(s => {
    lookupBtn.title = s.method === 'acoustid'
      ? 'Identify via AcoustID fingerprint'
      : 'Search MusicBrainz by title/artist/album'
    if (s.acoustid_configured && !s.fpcalc_available) {
      lookupBtn.title += ' (AcoustID key set but fpcalc not found — install libchromaprint-tools)'
    }
  }).catch(() => {})
  api.settings.get().then(s => {
    state.musicDirs = [s.default_music_dir ?? '', ...s.music_dirs].filter(Boolean)
    renameOnSave = s.rename_on_save
    renameTemplate = s.rename_template
  }).catch(() => {})
  api.tags.replaygainStatus().then(s => {
    replaygainAvailable = s.available
    updateBulkBar()
  }).catch(() => {})
  api.spectrogram.status().then(s => { spectrogramAvailable = s.available }).catch(() => {})
  await refreshUndoButton()
  resumeRunningJob()
  await loadLibrary()
  loadGenres()  // feeds the genre autocomplete in every mode
  await loadTracks()
}

init()
