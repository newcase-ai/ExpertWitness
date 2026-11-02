# Spec: structured expert profile for the profile page

**For:** backend
**From:** frontend (expert profile redesign)
**Date:** 2026-11-02

## Goal

The profile page shows an expert after research finishes. It has two modes:

- **Normal mode** is for the attorney looking at an expert. It shows facts about the expert, each with its sources.
- **Debug mode** is for us. It shows the same page plus research internals: confidence, conflicts, open leads, gaps, the run log and tool notes.

The target layout and content are the demo profiles in `ExpertsFrontEnd`, branch `demo-dri`, file `src/lib/expert-profile-mock.ts` (`ExpertProfile`). These are fictional mocks. We now want the backend to return the same kind of data, for real, from a research run.

Today `GET /dossiers/{id}/profile` returns the `Profile` in `app/models.py`. It is nine lists of free-text sentences (`text`, `refs`, `confidence`). The page can't build the header, contact block, career timeline, fee table or risk checks from free text without guessing. This spec asks for a structured view of the same research.

## What to build

1. **New endpoint** `GET /dossiers/{dossier_id}/expert-profile` that returns `ExpertProfileView` (below).
   - Keep `GET /dossiers/{id}/profile` as it is. Debug mode still uses it.
   - Return `409` while the run is queued or running, like `/findings` does. A failed run returns what it has.
2. **New endpoint** `GET /dossiers/{dossier_id}/files` for debug mode. It lists the downloaded files: `file_id`, `source_url`, `mime`, `bytes`, `retrieved_at`, `note_md`, and whether a text version exists. Nothing serves the `files` table today.
3. **Fill the structured profile from the CV and site text as well as from findings.** Most of the header and contact data (address, phone, website, career, education, fees) is only in downloaded CVs and practice websites. The current profile step reads findings only, so this data never reaches it.
   - Example from a real run: a CV's text had the business address and phone number, but neither appears in any finding or in the profile.
   - Either save these lines as findings when a file is read, or let the profile step read the file notes or text. Backend's choice, as long as every value can cite where it came from (see rules).

## Rules for every field

- **Every value cites its sources.** `refs` holds finding ids from this run; for a value taken from a file, a `file_id` is fine. The page shows the sources behind each value, so a value without refs is dropped (same rule as `items_of` today).
- **Unknown means `null` or `[]`. Never guess.** Don't fill a field from general knowledge or from "similar experts".
- **"None found" and "not checked" are different.** The page shows "None found" only when the research actually looked. Every section and every risk check carries a `status`:
  - `found`: there are entries.
  - `none_found`: we checked a real source and found nothing.
  - `not_checked`: no tool covered this, or the tool failed.
- **Side is never inferred.** Use `plaintiff` or `defense` only when a filing, opinion or transcript says who retained the expert. A reviewer hired by an insurer is not automatically `defense`. Otherwise use `unknown`.
- **Self-reported numbers stay labelled.** "120+ cases, about 60% defense" from a CV or directory is `source_kind: "self_reported"`. Counts we computed from court records are `"records"`. The page shows them differently.
- **Contact data is business contact only.** Include an address, phone or email only if the expert published it for professional contact (CV, practice website, expert directory). Never include a home address or personal social accounts. If the source labels a phone as a cell, keep the label in `note`.
- **Confidence stays on every value** (`high` or `low`) for debug mode. Normal mode never shows it.
- **Text is plain, short and factual.** Use the expert's name or "the expert", not "he" or "she", unless the source uses a pronoun.

## Response shape

TypeScript notation. Field names are snake_case to match the rest of the API.

```ts
type Ref = string                       // finding id (or file id for a value read from a file)
type Confidence = 'high' | 'low'
type SectionStatus = 'found' | 'none_found' | 'not_checked'
type SourceKind = 'records' | 'self_reported' | 'third_party'

interface Sourced<T> {
  value: T
  refs: Ref[]
  confidence: Confidence
  source_kind?: SourceKind
  note?: string | null                  // e.g. "listed as cell"
}

interface ExpertProfileView {
  dossier_id: string
  status: 'queued' | 'running' | 'done' | 'failed'
  updated_at: string
  version: number

  // Header
  name: string                                  // display name
  headline: Sourced<string> | null              // "Expert witness in accident reconstruction, vehicle dynamics and trucking collisions"
  expertise_tags: Sourced<string>[]             // 3–8 short tags: "Accident reconstruction", "Event data recorders"
  summary: Sourced<string> | null               // 3–5 sentence qualifications paragraph; every claim in it must be backed by refs

  contact: {
    based_in: Sourced<string> | null            // "Phoenix, Arizona"
    business_address: Sourced<string> | null
    current_role: Sourced<{ title: string; org: string }> | null
    phone: Sourced<string> | null
    email: Sourced<string> | null
    website: Sourced<string> | null             // the expert's own site, not a directory
  }

  // Key numbers (the demo's "highlights"); a label/value list so we can add rows without a schema change
  highlights: Array<Sourced<{ label: string; value: string }>>
  // Expected labels when known: "Experience", "In practice", "Board certification", "Licensed",
  // "Accreditation", "Cases reviewed", "Trial testimony", "Work split", "Has testified in"

  // Litigation
  cases: { status: SectionStatus; items: Case[] }
  expert_challenges: { status: SectionStatus; items: Challenge[] }   // Daubert / Frye / motions to exclude
  documents: { status: SectionStatus; items: CaseDocument[] }
  personal_litigation: { status: SectionStatus; items: PersonalCase[] }  // the expert as a party

  // Risk checks (each one shown as a row: clear / flagged / not checked)
  risk: {
    license_status: RiskCheck
    board_discipline: RiskCheck
    exclusion_lists: RiskCheck            // OIG LEIE and similar
    criminal_records: RiskCheck
    bankruptcies_liens_judgments: RiskCheck
    retractions: RiskCheck
  }

  // Background
  career: { status: SectionStatus; items: Role[] }
  education: { status: SectionStatus; items: Education[] }
  licenses: { status: SectionStatus; items: License[] }
  board_certifications: { status: SectionStatus; items: BoardCert[] }
  hospital_affiliations: { status: SectionStatus; items: Sourced<string>[] }
  memberships: { status: SectionStatus; items: Sourced<string>[] }
  fees: { status: SectionStatus; items: Fee[] }

  // Footprint
  publications: { status: SectionStatus; stats: AuthorStats | null; items: Publication[] }
  news: { status: SectionStatus; items: NewsItem[] }
  social: { status: SectionStatus; items: SocialProfile[] }    // professional accounts only
  online: { status: SectionStatus; items: OnlinePresence[] }   // practice site, CV, directory listings

  red_flags: { status: SectionStatus; items: Sourced<string>[] }  // anything opposing counsel could use
  not_him: { status: SectionStatus; items: Sourced<string>[] }    // same-name people ruled out

  sources: Array<{ name: string; url: string; kind: string }>     // distinct sources used, for the footer
}

interface Case {
  name: string                          // "Nguyen v. Desert Sun Transport, LLC"
  year: number | null
  court: string                         // short form: "D. Ariz.", "Maricopa Cnty. Super. Ct."
  docket_number: string | null
  url: string | null
  description: string | null            // one line: what the case was about
  side: 'plaintiff' | 'defense' | 'court' | 'unknown'
  involvement: 'deposition' | 'trial_testimony' | 'expert_report' | 'consultant_reviewer' | 'mentioned'
  outcome_for_expert: 'admitted' | 'limited' | 'excluded' | null   // only if a ruling says so
  refs: Ref[]
  confidence: Confidence
}

interface Challenge {
  case_name: string
  year: number | null
  kind: 'daubert' | 'frye' | 'motion_to_exclude' | 'other'
  ruling: 'admitted' | 'limited' | 'excluded' | 'pending' | 'unknown'
  summary: string
  refs: Ref[]
  confidence: Confidence
}

interface CaseDocument {
  kind: 'deposition_transcript' | 'trial_transcript' | 'expert_report' | 'exhibit' | 'affidavit' | 'cv' | 'fee_schedule' | 'other'
  title: string
  case_name: string | null
  year: number | null
  url: string | null
  file_id: string | null                // when we downloaded it
  refs: Ref[]
}

interface PersonalCase {
  name: string
  year: number | null
  court: string
  expert_role: 'plaintiff' | 'defendant' | 'other'
  summary: string
  disposition: string | null            // "Dismissed by agreement, 2017"
  refs: Ref[]
  confidence: Confidence
}

interface RiskCheck {
  status: 'clear' | 'flagged' | 'not_checked'
  summary: string                       // "Arizona license 00000 active, in good standing" / "No match in OIG LEIE"
  items: Sourced<string>[]              // the details behind a flag
  checked_sources: string[]             // what was searched, e.g. ["OIG LEIE", "Arizona Medical Board"]
}

interface Role { title: string; org: string; start_year: number | null; end_year: number | null; current: boolean; refs: Ref[]; confidence: Confidence }
interface Education { degree: string; field: string | null; institution: string; year: number | null; refs: Ref[]; confidence: Confidence }
interface License { kind: string; state: string; number: string | null; status: 'active' | 'expired' | 'unknown'; since: number | null; expires: string | null; refs: Ref[]; confidence: Confidence }
interface BoardCert { board: string; specialty: string; since: number | null; refs: Ref[]; confidence: Confidence }
interface Fee { service: string; amount: string; refs: Ref[]; source_kind: SourceKind }   // "$650/hr record review"
interface AuthorStats { works: number; citations: number; h_index: number | null; source: string }
interface Publication { title: string; venue: string | null; year: number | null; kind: 'article' | 'book' | 'chapter' | 'lecture' | 'abstract' | 'other'; url: string | null; cited_by: number | null; retracted: boolean; refs: Ref[] }
interface NewsItem { title: string; outlet: string | null; date: string | null; url: string; refs: Ref[] }
interface SocialProfile { platform: string; url: string; activity: 'active' | 'inactive' | 'unknown'; note: string | null; refs: Ref[] }
interface OnlinePresence { title: string; kind: 'practice_site' | 'cv' | 'expert_directory' | 'physician_directory' | 'other'; url: string; refs: Ref[] }
```

## Debug data (stays in the existing endpoints)

Debug mode shows everything above plus the following. None of it goes into `ExpertProfileView`.

| Debug item | Endpoint | Change needed |
|---|---|---|
| Confidence per value | `ExpertProfileView` (`confidence` fields) | none |
| Findings, tool notes, match scores | `GET /dossiers/{id}/findings` | none |
| Gaps | `GET /dossiers/{id}/findings` (`gaps`) | none |
| Open leads | `GET /dossiers/{id}/profile` (`open_leads`) | none |
| Run log | `GET /jobs/{job_id}` (`activity`), full trace in `/jobs/{job_id}/events` | none |
| Downloaded files and notes | `GET /dossiers/{id}/files` | **new** (see above) |
| Conflicts | `GET /dossiers/{id}/profile` (`conflicts`) | **add fields** (below) |

### Conflicts: make them readable

Today a conflict is one summary sentence plus refs, and nobody could tell which source said what. Add these fields to conflict items in `Profile.conflicts`:

```ts
interface ConflictItem {
  text: string                          // as today
  refs: Ref[]
  confidence: Confidence
  question: string                      // "Is the Arizona medical license active or expired?"
  claims: Array<{
    ref: Ref                            // must be in refs
    stance: string                      // short answer this source gives: "Active", "Expired"
    says: string                        // the source's own sentence, copied from the finding's facts
  }>
  next: string                          // how to settle it: "Look up license 00000 on the Arizona Medical Board site."
}
```

The code should check that `claims[].ref` is in `refs` and that `says` appears in that finding's facts, the same way `refs` are checked today.

## Mapping from the demo mock

| Demo field (`expert-profile-mock.ts`) | Field here | Where the data is today |
|---|---|---|
| `name`, `initials` | `name` (frontend makes the initials) | `candidate.display_name` |
| `title` | `headline` | Missing. `candidate.field` is close |
| `contact[]` Based in | `contact.based_in` | `candidate.location` |
| `contact[]` Business address, Phone, Website | `contact.*` | Only in CV and site text, never extracted |
| `contact[]` Currently | `contact.current_role` | Buried in `profile.identity` sentences |
| `tags` | `expertise_tags` | Missing. `profile.expertise` has long sentences |
| `qualifications` | `summary` | Missing |
| `highlights` | `highlights` | Partly in `profile.credentials` and `identity` sentences, and in web testimony facts |
| `cases` (side, involvement) | `cases` | `profile.cases` sentences plus `court_docket` facts. Side is always "not stated" today |
| `daubertChallenges` | `expert_challenges` | Missing (would come from opinions) |
| `documents` | `documents` | Partly: `files` table, docket document lists |
| `documents` "Personal litigation" | `personal_litigation` | Missing |
| `risk` | `risk.*` | Partly: `exclusion_check` tool, `retraction_check` tool, license findings |
| `publications` | `publications` | `work` and `author_stats` findings |
| `news`, `social`, `online` | `news`, `social`, `online` | `online` partly from `site_hit` findings. `news` and `social` missing |
| `career`, `education` | `career`, `education` | Only in CV text. Partly in `profile.identity` and `credentials` |
| `professional` (licenses, affiliations, memberships, fees) | `licenses`, `board_certifications`, `hospital_affiliations`, `memberships`, `fees` | Licenses and boards in `profile.credentials` sentences. The rest only in CV text |
| `sources` | `sources` | `profile.sources` (finding id to url and title) |

## Done when

- A run on an expert with a public CV returns the header (headline, contact, current role, tags, summary), career and education, all filled and each value citing refs.
- Every section and risk check has a `status`, and nothing shows `none_found` for a source that was never searched.
- No `side` value is set without a filing or opinion that says it.
- Conflicts in `/profile` carry `question`, `claims` and `next`.
- `GET /dossiers/{id}/files` exists.
- Tests use synthetic experts only (CLAUDE.md gotcha 8) and fake model responses (gotcha 9).
