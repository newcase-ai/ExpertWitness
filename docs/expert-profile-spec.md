# Spec: structured expert profile for the profile page

**For:** backend
**From:** frontend (expert profile redesign)
**Date:** 2026-11-03 (replaces the 2026-11-02 draft)

## Goal

The profile page should show an expert as clear blocks: a header (headline, specialty, tags), business
contact, career, education, licenses, cases, publications and risk checks. It should not show lists of
sentences.

Research already collects most of this. The problem is the format. `GET /dossiers/{id}/profile` returns
nine lists of free-text sentences, and the page can't lay those out without guessing.

**The change:** once research ends, one more model call organizes what the run collected into a
structured `ExpertProfileView`. The frontend reads it from a new endpoint. Research tools, findings and
the live `/profile` stay as they are.

## What a run has today

Checked against the 8 runs in the local DB (2 experts):

| Data | Where | Shape |
|---|---|---|
| Cases: name, court, docket number, date filed, nature of suit, match role | `court_docket` findings | JSON |
| Court documents: case, document number, date, page count, `file_id` | `court_file` findings | JSON |
| Papers (venue, year, DOI, citations, retracted); author stats (works, citations, h-index) | `work`, `author_stats` | JSON |
| Field, location | candidate record | JSON |
| Directory, CV and practice-site links | `site_hit` | URL and title only |
| License numbers, boards, career facts, self-reported testimony counts | `web_credential`, `web_fact`, `web_testimony`, `file_fact` | free-text sentences |
| Business address, phone, email, career roles with years, education, fees | **only the raw CV or site text** (`files.text_path`) | raw text |

Example: Karol Silva's CV text starts with the business address "204 E. 2nd Ave, Suite 241, San Mateo",
but the file note and the `ask_pdf` answer both dropped it. So the final step must read the CV and site
text itself, not just the findings.

## What to build

### 1. A final job step, `expert_profile`

In `jobs.run_research`, after `main_agent` and before `finish_job`, run a new module, `app/expert_profile.py`.

**Input to the model:**
- The candidate (name, field, location).
- Every finding of the run, one line each with its id. Leave out `work` and ruled-out `court_docket`:
  code handles those lists (see "Built by code").
- The text of each downloaded file that is **not** a court file (CVs, practice pages, directory pages),
  headed with its `file_id`. Cap each file and the total (for example 30k and 60k characters). CVs put
  contact and career details first, so cut from the end.

**Output:** the "model" fields of `ExpertProfileView` (below), as JSON with a schema.

**Code checks before saving** (same idea as `items_of` today):
- Every value cites `refs`: finding ids or file ids of this run. A value without valid refs is dropped.
- `phone`, `email`, `business_address` and `website` must appear in the text of a cited file or finding
  (ignore case and whitespace). Otherwise drop them, so the model can't invent contact data.
- Values outside the allowed enums are dropped.

**Where it's saved:** a new `expert_profiles` table (`dossier_id`, `data`, `updated_at`), owned by `store.py`.

**On failure:** a model error is a gap (`expert_profile` step), and the job still ends `done`. The page
falls back to "not available".

**Rebuild:** `POST /dossiers/{id}/expert-profile/rebuild` runs the step again for a finished run. This
lets us fill in existing runs and try prompt changes without re-running research.

### 2. Built by code (no model)

These lists are copied straight from the findings when the endpoint is called:

- `cases` come from `court_docket` findings that are not "ruled out": name, court, docket number, year
  from `date_filed`, url, and `involvement` from `match.role`. A case the model puts under `not_him`
  (and its filings) is left out.
- `documents` come from `court_file` findings plus files in the `files` table.
- `publications` come from `work`; `publication_stats` comes from `author_stats`.
- `online` comes from `site_hit` and `web_*` URLs, without duplicates.
- `sources` covers every finding and file cited anywhere in the view.
- `status` for each section (see below).

### 3. Endpoints

- `GET /dossiers/{id}/expert-profile` returns `ExpertProfileView`. It returns `409` while the run is queued
  or running, like `/findings`. A finished run without a saved model part still returns the code-built
  part, with the model fields set to `null` or `[]`.
- `POST /dossiers/{id}/expert-profile/rebuild` (see above).
- `GET /dossiers/{id}/files` (debug mode) returns `file_id`, `source_url`, `mime`, `bytes`, `retrieved_at`,
  `note_md` and `has_text`.

## Rules for every field

- **Every value cites refs.** No refs means the value is dropped.
- **Unknown means `null` or `[]`.** Never fill a field from general knowledge.
- **"None found" is not "not checked".** Code sets `status` from the run's activity log, never the model:
  - `found`: the section has items.
  - `none_found`: a tool that covers the section ran successfully and found nothing.
  - `not_checked`: no covering tool ran, or it failed.

  | Section | Covered by tools |
  |---|---|
  | cases, documents | `court_records`, `court_research`, `court_search` |
  | publications | `publications` |
  | exclusion_lists | `exclusion_check` |
  | retractions | `retraction_check` |
  | everything from the CV (career, education, contact, fees, ...) | a CV or site file was read (`note_file`, `ask_pdf`, `read_web_page`) |

- **Side is never inferred.** `plaintiff` or `defense` only when a cited filing or opinion says who
  retained the expert. An insurer's reviewer is not automatically `defense`. Otherwise `unknown`.
- **Self-reported stays labelled.** "120+ cases, 60% defense" from a CV or directory is
  `source_kind: "self_reported"`. Counts from court records are `"records"`.
- **Business contact only.** Include an address, phone or email only if the expert published it for
  professional contact (CV, practice site, directory). No home address and no personal social accounts.
  If the source labels a phone as a cell, say so in `note`.
- **Confidence on every value** (`high` or `low`), shown only in debug mode.
- **Plain, short, factual text.** Use the name or "the expert", not "he" or "she".

## Response shape

TypeScript notation, snake_case. `[M]` means the final model call fills it. `[C]` means code fills it.

```ts
type Ref = string                 // finding id or file id of this run
type Confidence = 'high' | 'low'
type SectionStatus = 'found' | 'none_found' | 'not_checked'
type SourceKind = 'records' | 'self_reported' | 'third_party'

interface Sourced<T> {
  value: T
  refs: Ref[]
  confidence: Confidence
  source_kind?: SourceKind
  note?: string | null            // e.g. "listed as cell"
}
interface Section<T> { status: SectionStatus; items: T[] }

interface ExpertProfileView {
  dossier_id: string
  status: 'done' | 'failed'
  updated_at: string
  built: boolean                  // false when the model part is missing (model failed or never ran)

  // Header [M]
  name: string                                // [C] candidate.display_name
  headline: Sourced<string> | null            // "Geriatrician and internist, former hospital chief medical officer"
  specialties: Sourced<string>[]              // "Geriatric medicine", "Internal medicine"
  expertise_tags: Sourced<string>[]           // 3–8 short tags
  summary: Sourced<string> | null             // 3–5 sentences; every claim backed by refs

  // Contact [M], business only, checked against the cited source text
  contact: {
    based_in: Sourced<string> | null
    business_address: Sourced<string> | null
    phone: Sourced<string> | null
    email: Sourced<string> | null
    website: Sourced<string> | null           // the expert's own site, not a directory
    current_role: Sourced<{ title: string; org: string }> | null
  }

  // Key numbers [M]; labels such as "Experience", "Board certification", "Licensed",
  // "Depositions (4 yrs)", "Work split", "Publications"
  highlights: Array<Sourced<string> & { label: string }>

  // Background [M]
  career: Section<Role>
  education: Section<Education>
  licenses: Section<License>
  board_certifications: Section<BoardCert>
  affiliations: Section<Sourced<string>>      // hospital and academic appointments
  memberships: Section<Sourced<string>>
  fees: Section<Fee>
  testimony_summary: Section<Sourced<string>> // self-reported: "5 depositions in the past 4 years", "both sides"

  // Litigation [C]
  cases: Section<Case>
  documents: Section<CaseDocument>

  // Risk [C status, M summary]
  risk: {
    license_status: RiskCheck
    board_discipline: RiskCheck
    exclusion_lists: RiskCheck
    retractions: RiskCheck
  }
  red_flags: Section<Sourced<string>>         // [M] anything opposing counsel could use
  not_him: Section<Sourced<string>>           // [M] same-name people ruled out

  // Footprint [C]
  publications: Section<Publication> & { stats: AuthorStats | null }
  online: Section<OnlinePresence>
  sources: Array<{ ref: Ref; title: string; url: string; kind: string }>
}

interface Case {
  name: string; court: string; docket_number: string | null; year: number | null; url: string
  nature_of_suit: string | null
  involvement: string | null      // match.role as the court tool saved it: "consultant or reviewer", ...
  side: 'plaintiff' | 'defense' | 'unknown'
  refs: Ref[]; confidence: Confidence
}
interface CaseDocument {
  title: string; case_name: string | null; date: string | null; url: string
  page_count: number | null; file_id: string | null; refs: Ref[]
}
interface RiskCheck {
  status: 'clear' | 'flagged' | 'not_checked'
  summary: string                 // "No match in OIG LEIE" / "Arizona license 13780 listed as expired (WebMD)"
  items: Sourced<string>[]
  checked_sources: string[]
}
interface Role { title: string; org: string; location: string | null; start_year: number | null; end_year: number | null; current: boolean; refs: Ref[]; confidence: Confidence }
interface Education { degree: string; field: string | null; institution: string; year: number | null; refs: Ref[]; confidence: Confidence }
interface License { kind: string; state: string | null; number: string | null; status: 'active' | 'expired' | 'unknown'; since: number | null; refs: Ref[]; confidence: Confidence }
interface BoardCert { board: string | null; specialty: string; since: number | null; refs: Ref[]; confidence: Confidence }
interface Fee { service: string; amount: string; refs: Ref[]; source_kind: SourceKind }
interface AuthorStats { works: number; citations: number; h_index: number | null; source: string }
interface Publication { title: string; venue: string | null; year: number | null; url: string; cited_by: number | null; retracted: boolean; refs: Ref[] }
interface OnlinePresence { title: string; url: string; kind: 'cv' | 'practice_site' | 'expert_directory' | 'physician_directory' | 'other'; refs: Ref[] }
```

## Left out on purpose

- **News, social accounts, personal litigation, criminal records, bankruptcies and liens:** no tool
  collects them. Add a section when a tool exists.
- **Daubert and Frye challenges, and the expert's outcome in a case:** the court tools don't read rulings
  for this yet. Add them with that tool.
- **Readable conflicts** (`question`, `claims`, `next` on `/profile` conflicts): debug only. This is
  separate work.
- **Debug data** (findings, gaps, open leads, run log, tool notes) stays in the existing endpoints.

## Done when

- A finished run on an expert with a public CV returns headline, specialties, contact (with business
  address and phone when the CV has them), current role, career and education. Each value cites refs.
- No contact value is returned unless it appears in the cited source text.
- Every section and risk check has a `status`, and `none_found` appears only where a covering tool ran.
- No `side` other than `unknown` is returned without a filing that says it.
- `rebuild` fills in an existing run. `/files` exists.
- Tests use synthetic experts and fake model replies (gotchas 8, 9): one CV-backed run, one run where the
  model fails (job still `done`, `built: false`), and one invented phone number that gets dropped.
