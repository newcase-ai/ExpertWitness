// The top bar on every page: a floating capsule with the name and a Search / Searches switch. Searches
// shows how many research runs are going, checked every POLL_MS.
import { useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { listSearches } from '@/lib/api'
import { cn } from '@/lib/utils'

const POLL_MS = 10_000

function useRunningCount() {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let live = true
    const load = () =>
      listSearches()
        .then(
          (searches) =>
            live &&
            setCount(
              searches
                .flatMap((s) => s.research)
                .filter((r) => r.status === 'running' || r.status === 'queued').length
            )
        )
        .catch(() => {}) // the count is a hint only: keep the last one
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [])
  return count
}

const tab = ({ isActive }: { isActive: boolean }) =>
  cn(
    'inline-flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-[13px] font-medium',
    isActive
      ? 'bg-card text-foreground shadow-[0_1px_3px_rgb(0_0_0/0.08)]'
      : 'text-muted-foreground hover:text-foreground'
  )

export default function AppHeader() {
  const running = useRunningCount()
  return (
    <div className="relative z-20 px-4 pt-3.5">
      <header
        className={cn(
          'mx-auto flex max-w-[1120px] flex-wrap items-center justify-between gap-3 rounded-[14px] border bg-card/80 py-2 pl-3.5 pr-2 shadow-[0_10px_30px_-18px_rgb(20_30_80/0.35)] backdrop-blur-md'
        )}
      >
        <Link to="/" className="flex items-center gap-2.5">
          <span className="grid size-7 place-items-center rounded-[7px] bg-primary font-serif text-base font-semibold leading-none text-primary-foreground">
            E
          </span>
          <span className="font-serif text-[17px] font-semibold">ExpertWitness</span>
        </Link>
        <nav aria-label="Main" className="flex gap-0.5 rounded-[10px] bg-[#F5F6F8] p-[3px]">
          <NavLink to="/" end className={tab}>
            Search
          </NavLink>
          <NavLink to="/searches" className={tab}>
            Searches
            {running > 0 && (
              <span
                title={`${running} research ${running === 1 ? 'run' : 'runs'} going`}
                className="rounded-full bg-[#E4EEFB] px-1.5 text-[11px] font-bold text-[#1F4E8C]"
              >
                {running}
              </span>
            )}
          </NavLink>
        </nav>
      </header>
    </div>
  )
}
