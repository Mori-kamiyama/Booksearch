export interface StableTagEvent {
  key: string
  tagIds: number[]
}

export class StableTagTracker {
  private candidateKey = ''
  private candidateCount = 0
  private stableKey = ''

  constructor(private readonly requiredFrames = 3) {}

  update(tagIds: number[]): StableTagEvent | null {
    const normalized = [...new Set(tagIds)].sort((a, b) => a - b)
    const key = normalized.join(',')
    if (!key) {
      this.candidateKey = ''
      this.candidateCount = 0
      return null
    }

    if (key === this.candidateKey) this.candidateCount += 1
    else {
      this.candidateKey = key
      this.candidateCount = 1
    }

    if (this.candidateCount < this.requiredFrames || key === this.stableKey) return null
    this.stableKey = key
    return { key, tagIds: normalized }
  }

  reset(): void {
    this.candidateKey = ''
    this.candidateCount = 0
    this.stableKey = ''
  }
}
