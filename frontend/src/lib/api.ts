import type { Book, ShelfCandidate } from './types'

export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export function apiUrl(path: string): string {
  return API_BASE ? `${API_BASE}${path}` : path
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiUrl(path), init)
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init)
  if (!res.ok) {
    throw new Error(`request failed: ${res.status}`)
  }
  return res.json() as Promise<T>
}

export async function searchBooks(query: string, limit = 30): Promise<Book[]> {
  const data = await jsonFetch<{ books?: Book[] }>(`/api/books/search?q=${encodeURIComponent(query)}&limit=${limit}`)
  return data.books ?? []
}

export async function getBook(id: string | number): Promise<Book> {
  return jsonFetch<Book>(`/api/books/${id}`)
}

export async function getShelfCandidates(limit = 1000): Promise<ShelfCandidate[]> {
  const data = await jsonFetch<{ candidates?: ShelfCandidate[] }>(`/api/shelf-candidates?limit=${limit}`)
  return data.candidates ?? []
}
