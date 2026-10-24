export interface ConvertResult {
  id: string
  fileName: string
  fileType: string
  originalSize: number
  pageCount: number | null
  durationMs: number
  conversionDurationMs: number
  tokenCountingDurationMs: number
  markdown: string
  markdownCharacters: number
  markdownWords: number
  baselineCharacters: number | null
  baselineWords: number | null
  baselineAvailable: boolean
  baselineStatus: 'complete' | 'incomplete' | 'unavailable'
  baselineReason: string
  extractionCoveragePercent: number
  extractedTextPageCount: number
  imagePageCount: number
  originalPdfCount: TokenCountResult
  markdownCount: TokenCountResult
  originalPdfInputTokens: number | null
  markdownInputTokens: number | null
  tokensSaved: number | null
  reductionPercent: number | null
  comparisonComparable: boolean
  comparisonReason: string | null
  tokenCountingStatus: string
  tokenCountingModel: string
  tokenCountingMethod: string
  mode: string
  forceOcr: boolean
  success: boolean
  error?: string | null
}

export interface TokenCountResult {
  status: string
  inputTokens: number | null
  model: string
  method: string
  timestamp: string | null
  error: string | null
  cached: boolean
}
