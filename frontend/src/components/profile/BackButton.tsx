import { useNavigate } from 'react-router-dom'

export function BackButton({ className, children }: { className?: string; children?: React.ReactNode }) {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(-1)} className={className}>
      {children}
      Back to results
    </button>
  )
}
