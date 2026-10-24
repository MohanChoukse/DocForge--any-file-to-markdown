import { Zap } from 'lucide-react'

interface Props {
  health: { status: string; modelsLoaded: boolean; device: string } | null
}

export default function Header({ health }: Props) {
  const ok = health?.status === 'ok' && health.modelsLoaded
  const label = !health ? 'Connecting…' : ok ? `Ready · ${health.device}` : 'Loading models…'

  return (
    <header className="app-header">
      <div className="app-header__logo">
        <Zap size={20} />
        <span>DocForge</span>
        <span style={{ fontSize: '0.65rem', fontWeight: 500, color: 'var(--fg-dim)', marginLeft: '0.25rem' }}>
          PDF to Markdown · Local Marker
        </span>
      </div>
      <div className="health-badge">
        <span className={`health-dot ${ok ? 'health-dot--ok' : health ? '' : 'health-dot--error'}`} />
        {label}
      </div>
    </header>
  )
}
