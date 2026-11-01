import { Link, NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'

export default function AppHeader() {
  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b bg-card px-6">
      <Link to="/" className="font-serif text-lg font-semibold">
        ExpertWitness
      </Link>
      <nav aria-label="Main" className="flex gap-5 text-sm font-medium">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            cn(isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground')
          }
        >
          Search
        </NavLink>
        <NavLink
          to="/searches"
          className={({ isActive }) =>
            cn(isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground')
          }
        >
          Searches
        </NavLink>
      </nav>
    </header>
  )
}
