import { useState, useEffect } from 'react'
import Header from './components/Header'
import ConverterPage from './pages/ConverterPage'

export default function App() {
  const [health, setHealth] = useState<{ status: string; modelsLoaded: boolean; device: string } | null>(null)

  useEffect(() => {
    const check = () => {
      fetch('/api/health')
        .then(r => r.json())
        .then(d => setHealth(d))
        .catch(() => setHealth(null))
    }
    check()
    const id = setInterval(check, 15_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="app-shell">
      <Header health={health} />
      <ConverterPage />
    </div>
  )
}
