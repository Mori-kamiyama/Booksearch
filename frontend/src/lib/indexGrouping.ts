export const KANA_ROWS: Array<[string, string]> = [
  ['あ', 'あいうえおぁぃぅぇぉ'],
  ['か', 'かきくけこがぎぐげご'],
  ['さ', 'さしすせそざじずぜぞ'],
  ['た', 'たちつてとだぢづでどっ'],
  ['な', 'なにぬねの'],
  ['は', 'はひふへほばびぶべぼぱぴぷぺぽ'],
  ['ま', 'まみむめも'],
  ['や', 'やゆよゃゅょ'],
  ['ら', 'らりるれろ'],
  ['わ', 'わをんゎ'],
]

const kanaGroupOrder = new Map(KANA_ROWS.map(([label], index) => [label, index]))
const titleCollator = new Intl.Collator('ja')
const groupCollator = new Intl.Collator('ja', { sensitivity: 'base' })

export function indexGroupForTitle(title: string): string {
  const normalized = title.trim().normalize('NFKC')
  const ch = normalized.charAt(0)
  if (!ch) return 'その他'

  const code = ch.codePointAt(0) ?? 0
  const hira = code >= 0x30a1 && code <= 0x30f6 ? String.fromCodePoint(code - 0x60) : ch
  for (const [label, chars] of KANA_ROWS) {
    if (chars.includes(hira)) return label
  }
  if (/[A-Za-z]/.test(ch)) return ch.toUpperCase()
  if (/[0-9]/.test(ch)) return ch
  if (/\p{Script=Han}/u.test(ch)) return ch
  return 'その他'
}

export function groupBooksForIndex<T extends { title: string }>(books: T[]): Array<{ label: string, books: T[] }> {
  const grouped = new Map<string, T[]>()
  for (const book of [...books].sort((a, b) => titleCollator.compare(a.title, b.title))) {
    const label = indexGroupForTitle(book.title)
    const group = grouped.get(label) ?? []
    group.push(book)
    grouped.set(label, group)
  }

  return [...grouped.entries()]
    .sort(([left], [right]) => compareIndexGroupLabels(left, right))
    .map(([label, groupedBooks]) => ({ label, books: groupedBooks }))
}

function compareIndexGroupLabels(left: string, right: string): number {
  const leftRank = groupRank(left)
  const rightRank = groupRank(right)
  return leftRank === rightRank ? groupCollator.compare(left, right) : leftRank - rightRank
}

function groupRank(label: string): number {
  if (kanaGroupOrder.has(label)) return kanaGroupOrder.get(label)!
  if (/^[A-Z]$/.test(label)) return 10 + label.charCodeAt(0) - 65
  if (/^[0-9]$/.test(label)) return 36 + Number(label)
  if (/^\p{Script=Han}$/u.test(label)) return 46
  return 47
}
