export interface Book {
  id: number
  title: string
  authors: string
  publisher: string
  published_date: string
  class_number: string
  registration_number: string
  isbn: string
  thumbnail: string | null
  info_link: string | null
  description?: string
  summary?: string
  reading_time_minutes?: number
  shelf_ids?: string[]
  shelf_candidates?: ShelfCandidate[]
}

export interface ShelfCandidate {
  book_id?: number
  title?: string
  shelf_id: string
  confidence: number
  observations: number
  last_seen_at?: string
  updated_at?: string
  avg_score?: number
  crop_url?: string
  thumbnail?: string | null
}

export interface Job {
  job_id: string
  status: string
  error?: string
}

export interface EmptyRuleRegion {
  cols: number[]
  rows: number[]
}

export interface LayoutUnit {
  unit: string
  kind: 'base' | 'side'
  unit_index_from_entrance?: number
  unit_index?: number
  cols: number
  rows: number
  mirrored?: boolean
  empty_rule: { regions: EmptyRuleRegion[] } | null
}

export interface LayoutSlot {
  slot_id: string
  shelf_id: string | null
  recognition_code: string | null
  kind: 'base' | 'side'
  unit: string
  unit_index_from_entrance?: number
  unit_index?: number
  col: number
  row: number
  status: 'usable' | 'empty'
  label_ja: string
}
