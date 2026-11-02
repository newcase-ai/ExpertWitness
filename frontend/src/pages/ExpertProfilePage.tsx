import { useParams } from 'react-router-dom'
import AppHeader from '@/components/AppHeader'
import { BackButton } from '@/components/profile/BackButton'
import { ExpertProfileView } from '@/components/profile/ExpertProfileView'
import { DEMO_PROFILES } from '@/lib/mocks/expert-profile'

// Research for one of the experts the search returns. The research is mocked: sections fill in
// one step at a time over the canned profile.
export default function ExpertProfilePage() {
  const { expertId: demoId = '' } = useParams()
  const profile = DEMO_PROFILES[demoId]
  if (!profile)
    return (
      <div className="min-h-screen text-sm">
        <AppHeader />
        <main className="mx-auto max-w-[640px] space-y-3 px-4 pt-24">
          <h1 className="text-xl font-semibold">No research data for this expert yet</h1>
          <p className="text-muted-foreground">
            This demo has research for Dr. Margaret Chen and Dr. Robert Alvarez only.
          </p>
          <BackButton className="font-medium text-primary hover:underline" />
        </main>
      </div>
    )
  return <ExpertProfileView key={demoId} demoId={demoId} p={profile} />
}
