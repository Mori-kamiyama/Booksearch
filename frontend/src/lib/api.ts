export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export function apiUrl(path: string): string {
  return API_BASE ? `${API_BASE}${path}` : path
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiUrl(path), init)
}
