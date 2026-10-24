import { useState, useRef, useCallback, useEffect } from 'react'
import {
  Upload, FileText, Clock, Copy, Download, Check, X,
} from 'lucide-react'
import type { ConvertResult } from '../types'

type Tab = 'preview' | 'raw'

/** Simple Markdown-to-HTML renderer (no deps) */
function renderMd(md: string): string {
  let html = md
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    // headings
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // bold / italic
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // code blocks
    .replace(/```[\s\S]*?```/g, m => {
      const inner = m.slice(3, -3).replace(/^\w*\n/, '')
      return `<pre><code>${inner}</code></pre>`
    })
    // inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // blockquotes
    .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
    // unordered list
    .replace(/^[*-] (.+)$/gm, '<li>$1</li>')
    // paragraphs
    .replace(/\n{2,}/g, '</p><p>')
  return `<p>${html}</p>`
}

function formatDuration(ms: number) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

export default function ConverterPage() {
  // ── State ──────────────────────────────────────────────────────
  const [file, setFile] = useState<File | null>(null)
  const [mode, setMode] = useState<string>('auto')
  const [forceOcr, setForceOcr] = useState(false)
  const [converting, setConverting] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState<ConvertResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('preview')
  const [copied, setCopied] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const requestInFlightRef = useRef(false)

  // ── Timer ──────────────────────────────────────────────────────
  useEffect(() => {
    if (converting) {
      setElapsed(0)
      timerRef.current = setInterval(() => setElapsed(e => e + 100), 100)
    } else if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [converting])

  // ── File handling ──────────────────────────────────────────────
  const handleFile = useCallback((f: File) => {
    setFile(f)
    setResult(null)
    setError(null)
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [handleFile])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(true)
  }, [])

  const onDragLeave = useCallback(() => setDragActive(false), [])

  // ── Convert ────────────────────────────────────────────────────
  const convert = async () => {
    if (!file || requestInFlightRef.current) return
    requestInFlightRef.current = true
    setConverting(true)
    setError(null)
    setResult(null)

    const formData = new FormData()
    formData.append('file', file)
    if (mode !== 'auto') formData.append('mode', mode)
    formData.append('force_ocr', String(forceOcr))

    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 15 * 60 * 1000)
    try {
      const res = await fetch('/api/convert', {
        method: 'POST',
        body: formData,
        signal: controller.signal,
      })
      const data = await res.json().catch(() => null)
      if (!res.ok || !data?.success) {
        const detail = data?.error ?? data?.detail
        throw new Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `Conversion failed (HTTP ${res.status})`)
      }
      const converted = data.result as ConvertResult | undefined
      if (!converted || typeof converted.markdown !== 'string' || !converted.markdown.trim()) {
        throw new Error('The API response did not contain non-empty result.markdown')
      }
      setResult(converted)
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        setError('Conversion timed out after 15 minutes. Check the DocForge API logs before retrying.')
      } else {
        setError(err instanceof Error ? err.message : 'Network error while converting the PDF')
      }
    } finally {
      clearTimeout(timeoutId)
      requestInFlightRef.current = false
      setConverting(false)
    }
  }

  // ── Copy / Download ────────────────────────────────────────────
  const copyMd = () => {
    if (!result) return
    navigator.clipboard.writeText(result.markdown)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const downloadMd = () => {
    if (!result) return
    const baseName = result.fileName.replace(/\.[^.]+$/, '') || 'converted'
    const blob = new Blob([result.markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `${baseName}.md`; a.click()
    URL.revokeObjectURL(url)
  }

  const reset = () => {
    setFile(null); setResult(null); setError(null)
  }

  return (
    <main className="app-main">
      <div className="animate-in">
        <h1>DocForge <span className="heading-subtitle">PDF to Markdown</span></h1>
        <p style={{ color: 'var(--fg-muted)', fontSize: '0.85rem', marginTop: '0.25rem' }}>
          Convert documents locally with Marker.
        </p>
      </div>

      {/* ── Upload area ─────────────────────────────────────────── */}
      {!result && (
        <div style={{ marginTop: '1.5rem' }} className="animate-in delay-1">
          <div
            className={`drop-zone ${dragActive ? 'drop-zone--active' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".pdf"
              className="sr-only"
              disabled={converting}
              onChange={e => {
                const f = e.target.files?.[0]
                if (f) handleFile(f)
              }}
            />
            {file ? (
              <div className="flex items-center gap-2">
                <FileText size={24} style={{ color: 'var(--accent)' }} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>{file.name}</div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--fg-muted)' }}>
                    {(file.size / 1024).toFixed(1)} KB — Click or drop to change
                  </div>
                </div>
              </div>
            ) : (
              <>
                <Upload size={32} className="drop-zone__icon" />
                <div className="drop-zone__text">
                  <strong>Click or drag</strong> a PDF file to convert
                </div>
                <div style={{ fontSize: '0.65rem', color: 'var(--fg-dim)', marginTop: '0.35rem' }}>
                  Supports PDF files • Processed locally with Marker
                </div>
              </>
            )}
          </div>

          {/* ── Options bar ───────────────────────────────────────── */}
          <div className="options-bar" style={{ marginTop: '0.75rem' }}>
            <div className="option-group">
              <span className="option-label">Mode</span>
              <select value={mode} onChange={e => setMode(e.target.value)} className="option-select">
                <option value="auto">Auto (device default)</option>
                <option value="fast">Fast (CPU-optimized)</option>
                <option value="balanced">Balanced (GPU/VLM)</option>
              </select>
            </div>
            <label className="option-toggle">
              <input type="checkbox" checked={forceOcr} onChange={e => setForceOcr(e.target.checked)} />
              Disable text extraction (Force OCR)
            </label>
            <div style={{ marginLeft: 'auto' }}>
              <button
                className="btn btn-primary"
                disabled={!file || converting}
                onClick={convert}
              >
                {converting ? (
                  <>
                    <span className="spinner" style={{ width: 14, height: 14, borderWidth: '1.5px' }} />
                    Converting…
                  </>
                ) : (
                  'Convert to Markdown'
                )}
              </button>
            </div>
          </div>

          {/* Status bar */}
          {converting && (
            <div className="status-bar mt-2 animate-in">
              <span className="spinner" />
              <span style={{ fontSize: '0.8rem' }}>
                Processing <strong>{file?.name}</strong> with Marker ({mode === 'auto' ? 'auto' : mode} mode)…
              </span>
              <span className="elapsed">{(elapsed / 1000).toFixed(1)}s</span>
            </div>
          )}

          {error && (
            <div className="info-box info-box--error mt-2 animate-in">
              <X size={14} style={{ flexShrink: 0, marginTop: 2 }} />
              <span>{error}</span>
            </div>
          )}
        </div>
      )}

      {/* ── Result ──────────────────────────────────────────────── */}
      {result && (
        <div style={{ marginTop: '1.5rem' }}>
          {/* Summary bar */}
          <div className="flex items-center justify-between animate-in">
            <div className="flex items-center gap-2">
              <FileText size={18} style={{ color: 'var(--accent)' }} />
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>{result.fileName}</div>
                <div style={{ fontSize: '0.65rem', color: 'var(--fg-muted)' }}>
                  {result.pageCount != null && `${result.pageCount} pages · `}
                  {result.mode} mode · {formatDuration(result.durationMs)}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn btn-ghost btn-sm" onClick={copyMd}>
                {copied ? <Check size={14} /> : <Copy size={14} />}
                {copied ? 'Copied!' : 'Copy'}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={downloadMd}>
                <Download size={14} /> Download .md
              </button>
              <button className="btn btn-ghost btn-sm" onClick={reset}>
                New file
              </button>
            </div>
          </div>

          {/* Duration badge */}
          <div className="flex items-center gap-2 mt-2 animate-in delay-1" style={{ fontSize: '0.75rem', color: 'var(--fg-muted)' }}>
            <Clock size={14} />
            Processing time: <strong>{formatDuration(result.durationMs)}</strong>
          </div>

          {/* Preview / Raw tabs */}
          <div className="card mt-2 animate-in delay-1">
            <div className="tabs">
              <button className={`tab ${tab === 'preview' ? 'tab--active' : ''}`} onClick={() => setTab('preview')}>
                Preview
              </button>
              <button className={`tab ${tab === 'raw' ? 'tab--active' : ''}`} onClick={() => setTab('raw')}>
                Raw Markdown
              </button>
            </div>
            {tab === 'preview' ? (
              <div
                className="preview-pane"
                dangerouslySetInnerHTML={{ __html: renderMd(result.markdown) }}
              />
            ) : (
              <div className="raw-pane">{result.markdown}</div>
            )}
          </div>

        </div>
      )}
    </main>
  )
}
