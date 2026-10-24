import type { ConvertResult, TokenCountResult } from '../types'

function countLabel(count: TokenCountResult) {
  return count.inputTokens == null ? count.status : count.inputTokens.toLocaleString()
}

function timestampLabel(value: string | null) {
  return value ? new Date(value).toLocaleString() : 'No API request made'
}

export default function TokenSavingsPanel({ result }: { result: ConvertResult }) {
  const canCompare = result.comparisonComparable && result.tokensSaved != null
  const savedNote = canCompare
    ? result.tokensSaved! < 0
      ? `Markdown uses ${Math.abs(result.tokensSaved!).toLocaleString()} more input tokens.`
      : `Estimated reduction for ${result.tokenCountingModel}.`
    : result.comparisonReason ?? 'Both API verified counts are required.'
  const cards = [
    { title: 'Original PDF input tokens', value: countLabel(result.originalPdfCount), note: result.originalPdfCount.error ?? `Count status: ${result.originalPdfCount.status}` },
    { title: 'Markdown input tokens', value: countLabel(result.markdownCount), note: result.markdownCount.error ?? `Count status: ${result.markdownCount.status}` },
    { title: 'Estimated tokens saved', value: canCompare ? result.tokensSaved!.toLocaleString() : 'Not comparable', note: savedNote },
    { title: 'Reduction percentage', value: canCompare ? `${result.reductionPercent?.toFixed(2)}%` : 'Not comparable', note: canCompare ? 'Model-specific; negative values mean more Markdown input tokens.' : result.comparisonReason ?? 'Waiting for both counts.' },
  ]

  return <section className="savings-panel card" aria-labelledby="analysis-heading">
    <div className="card__header"><div><h2 id="analysis-heading">Model-specific token analysis</h2><p className="analysis-intro">Counts reflect the selected model’s current input handling. Results can differ across models.</p></div></div>
    <div className="card__body">
      <div className="stats-grid">
        {cards.map(card => <article className="stat-box" key={card.title}><div className="stat-label">{card.title}</div><div className="stat-value analysis-count">{card.value}</div><div className="stat-sub">{card.note}</div></article>)}
      </div>
      <div className={`coverage-box ${result.tokenCountingStatus === 'API verified' ? 'coverage-box--complete' : ''}`}>
        <strong>Counting status: {result.tokenCountingStatus}</strong>
        <span>Selected model: <b>{result.tokenCountingModel}</b></span>
        <span>Method: {result.tokenCountingMethod}</span>
        <span>PDF count time: {timestampLabel(result.originalPdfCount.timestamp)}{result.originalPdfCount.cached ? ' · cached' : ''}</span>
        <span>Markdown count time: {timestampLabel(result.markdownCount.timestamp)}{result.markdownCount.cached ? ' · cached' : ''}</span>
        {result.comparisonReason && <p>{result.comparisonReason}</p>}
      </div>
      <div className="coverage-box mt-2">
        <strong>Local extraction diagnostic: {result.baselineStatus}</strong>
        <span>{result.pageCount ?? '—'} pages · {result.extractedTextPageCount} with selectable text · {result.imagePageCount} image pages · {result.extractionCoveragePercent}% covered pages · {result.baselineCharacters?.toLocaleString() ?? '—'} extracted characters</span>
        <p>{result.baselineReason}</p>
      </div>
      <div className="analysis-footer"><span>Local conversion: <strong>{(result.conversionDurationMs / 1000).toFixed(1)}s</strong></span><span>API counting: <strong>{(result.tokenCountingDurationMs / 1000).toFixed(1)}s</strong> · total request: {(result.durationMs / 1000).toFixed(1)}s</span><span>Token savings apply only to {result.tokenCountingModel}.</span></div>
    </div>
  </section>
}
