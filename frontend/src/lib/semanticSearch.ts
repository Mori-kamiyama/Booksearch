// Keep identifier searches and the user's explicit original-query search literal.
export function semanticQueryAllowed(query: string, exactOnly = false): boolean {
  const q = query.normalize('NFKC').trim()
  if (exactOnly || !q || Array.from(q).length > 200) return false
  const isbn = q.toLowerCase().replace(/^isbn\s*:?\s*/, '').replace(/[-\s]/g, '')
  if (/^(?:[0-9x]{10}|[0-9x]{13})$/.test(isbn)) return false
  return /[\p{L}\p{N}]/u.test(q)
}
