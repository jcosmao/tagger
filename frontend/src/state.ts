import { Track, Artist, Album, Genre, Label, ArtistEntry, ArtistDetail, ScanJob, LookupResult, IssueCount } from './api'

export const PAGE_SIZE = 100
export const DEFAULT_COLS = ['quality', 'title', 'artist', 'album', 'album_artist', 'year', 'track_number', 'format', 'duration']
// Shown whatever the saved column preferences say.
export const ALWAYS_COLS = ['album_artist']
const COLS_KEY = 'tagger_visible_cols'

export function loadColPrefs(): Set<string> {
  try {
    const saved = localStorage.getItem(COLS_KEY)
    if (saved) return new Set([...JSON.parse(saved), ...ALWAYS_COLS])
  } catch { /* ignore */ }
  return new Set(DEFAULT_COLS)
}

export function saveColPrefs(cols: Set<string>): void {
  localStorage.setItem(COLS_KEY, JSON.stringify([...cols]))
}

// Sidebar lists: alphabetical, or busiest first.
export type NavSort = 'name' | 'count'
const SORT_KEY = 'tagger_nav_sort'

export function loadNavSort(): Record<'genres' | 'labels', NavSort> {
  try {
    return { genres: 'name', labels: 'name', ...JSON.parse(localStorage.getItem(SORT_KEY) || '{}') }
  } catch {
    return { genres: 'name', labels: 'name' }
  }
}

export function saveNavSort(sort: Record<'genres' | 'labels', NavSort>): void {
  try { localStorage.setItem(SORT_KEY, JSON.stringify(sort)) } catch { /* ignore */ }
}

export type TagField = 'title' | 'artist' | 'album' | 'album_artist' | 'year' | 'track_number' | 'disc_number' | 'genre' | 'comment' | 'composer' | 'bpm' | 'label' | 'lyrics'

// Text/textarea fields driven by the generic form loop. `compilation` is a
// boolean checkbox handled separately in populateForm/saveTags.
export const TAG_FIELDS: TagField[] = [
  'title', 'artist', 'album', 'album_artist', 'year',
  'track_number', 'disc_number', 'genre', 'comment', 'composer', 'bpm', 'label', 'lyrics',
]

export interface DirNode {
  name:     string
  path:     string
  children: DirNode[] | null
  loading:  boolean
  expanded: boolean
}

export type SidebarMode = 'tags' | 'artists' | 'genres' | 'labels' | 'files' | 'quality'

export interface State {
  // sidebar
  sidebarMode:       SidebarMode
  // tags panel
  artists:           Artist[]
  albumsByArtist:    Map<string, Album[]>
  selectedArtist:    string | null
  selectedAlbum:     string | null
  expandedArtists:   Set<string>
  // artists panel
  artistEntries:     ArtistEntry[]
  selectedArtistKey: string | null
  artistFilter:      string
  artistDetail:      ArtistDetail | null
  // genres panel
  genres:            Genre[]
  selectedGenre:     string | null
  navSort:           Record<'genres' | 'labels', NavSort>
  // labels panel
  labels:            Label[]
  selectedLabel:     string | null
  labelFilter:       string
  genreFilter:       string
  // files panel
  rootNode:          DirNode | null
  selectedDirectory: string | null
  // track list
  tracks:            Track[]
  total:             number
  selectedIds:       Set<number>
  query:             string
  // scan
  scanJob:           ScanJob | null
  scanPollTimer:     ReturnType<typeof setInterval> | null
  // columns
  visibleCols:       Set<string>
  colPickerOpen:     boolean
  // view
  viewMode:          'list' | 'albums'
  // quality panel
  qualityIssues:     IssueCount | null
  selectedIssue:     string | null
  // pending cover from lookup
  pendingCoverAlbumId: string | null
  // pending MB IDs from lookup result (written on save)
  pendingLookupResult: LookupResult | null
  // incremented after every cover write to bust browser cache
  coverBust: number
  // sorting
  sortKey:           string | null
  sortDir:           'asc' | 'desc'
  // filters
  filterFormat:      string
  filterQuality:     string
  // configured music dirs (for source column)
  musicDirs:         string[]
  // pagination
  page:              number
}

export const state: State = {
  sidebarMode:       'tags',
  artists:           [],
  albumsByArtist:    new Map(),
  selectedArtist:    null,
  selectedAlbum:     null,
  expandedArtists:   new Set(),
  artistEntries:     [],
  selectedArtistKey: null,
  artistFilter:      '',
  artistDetail:      null,
  genres:            [],
  selectedGenre:     null,
  navSort:           loadNavSort(),
  labels:            [],
  selectedLabel:     null,
  labelFilter:       '',
  genreFilter:       '',
  rootNode:          null,
  selectedDirectory: null,
  tracks:            [],
  total:             0,
  selectedIds:       new Set(),
  query:             '',
  scanJob:           null,
  scanPollTimer:     null,
  visibleCols:       loadColPrefs(),
  colPickerOpen:     false,
  viewMode:          'list',
  qualityIssues:     null,
  selectedIssue:     null,
  pendingCoverAlbumId: null,
  pendingLookupResult: null,
  coverBust: 0,
  sortKey:           null,
  sortDir:           'asc',
  filterFormat:      '',
  filterQuality:     '',
  musicDirs:         [],
  page:              0,
}
