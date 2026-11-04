// Finished expert research, mocked. Fictional experts; the ids match the candidates the backend
// returns (backend/main.py). Swap in real research here once it exists.

import { ExpertCase, ExpertProfile, Entry, Fact, Involvement } from '@/lib/types'

// Transcript details come straight from the case list so the counts always match.
const transcripts = (cases: ExpertCase[], involvement: Involvement): Entry[] =>
  cases
    .filter((c) => c.involvement === involvement)
    .map((c) => ({ title: c.name, detail: `${c.year} · ${c.court} · ${c.description}` }))

const background = (license: string): Fact => ({
  label: 'Background check',
  value: 'No flags',
  details: [
    { title: 'Criminal records', detail: 'None found in state or federal court records' },
    { title: 'Bankruptcies, liens & judgments', detail: 'None found' },
    { title: 'License status', detail: license },
  ],
})

const CHEN_CASES: ExpertCase[] = [
  {
    year: 2025,
    name: 'Harmon v. Mid-Ohio Regional Medical Center',
    description: 'Alleged negligence in a failed lumbar fusion.',
    court: 'Franklin Cnty. C.P.',
    side: 'defense',
    involvement: 'Deposition',
  },
  {
    year: 2024,
    name: 'Delgado v. Kessler Freight Lines',
    description: 'Cervical disc injury after a rear-end collision.',
    court: 'S.D. Ohio',
    side: 'plaintiff',
    involvement: 'Deposition',
  },
  {
    year: 2023,
    name: 'Whitfield v. Grandview Surgical Associates',
    description: 'Hip replacement complication and standard of care.',
    court: 'Cuyahoga Cnty. C.P.',
    side: 'defense',
    involvement: 'Trial testimony',
  },
  {
    year: 2022,
    name: 'Okoro v. Lakeshore Mutual Insurance Co.',
    description: 'Disputed causation of a torn rotator cuff.',
    court: 'N.D. Ohio',
    side: 'plaintiff',
    involvement: 'Deposition',
  },
  {
    year: 2021,
    name: 'Brennan v. Columbus Metro Transit',
    description: 'Knee injury from a bus fall; expert report cited.',
    court: 'Ohio Ct. Cl.',
    side: 'unknown',
    involvement: 'Mentioned',
  },
  {
    year: 2019,
    name: 'Pruitt v. Summit Orthopedics, Inc.',
    description: 'Spinal hardware failure after fusion surgery.',
    court: 'S.D. Ohio',
    side: 'defense',
    involvement: 'Trial testimony',
  },
]

const ALVAREZ_CASES: ExpertCase[] = [
  {
    year: 2026,
    name: 'Nguyen v. Desert Sun Transport, LLC',
    description: 'Jackknifed tractor-trailer on I-10; speed and braking analysis.',
    court: 'D. Ariz.',
    side: 'plaintiff',
    involvement: 'Deposition',
  },
  {
    year: 2025,
    name: 'Saguaro Mutual Insurance v. Ortega',
    description: 'Intersection collision; signal timing and sightlines.',
    court: 'Maricopa Cnty. Super. Ct.',
    side: 'defense',
    involvement: 'Trial testimony',
  },
  {
    year: 2024,
    name: 'Campos v. Westline Freight, Inc.',
    description: 'Underride collision with a stopped trailer at night.',
    court: 'D. Nev.',
    side: 'plaintiff',
    involvement: 'Deposition',
  },
  {
    year: 2023,
    name: 'Hale v. City of Tucson',
    description: 'Pedestrian struck in a crosswalk; driver perception-reaction time.',
    court: 'Pima Cnty. Super. Ct.',
    side: 'defense',
    involvement: 'Deposition',
  },
  {
    year: 2022,
    name: 'Reyes v. Copperline Logistics',
    description: 'Rear-end truck crash; speed opinion partially excluded.',
    court: 'D.N.M.',
    side: 'defense',
    involvement: 'Deposition',
  },
  {
    year: 2021,
    name: 'Lindqvist v. Rapid Haul Co.',
    description: 'Lane-change sideswipe; EDR data reconciliation.',
    court: 'W.D. Tex.',
    side: 'plaintiff',
    involvement: 'Trial testimony',
  },
  {
    year: 2019,
    name: 'Arizona v. Doe',
    description: 'Vehicular manslaughter; prosecution expert report cited.',
    court: 'Ariz. Ct. App.',
    side: 'unknown',
    involvement: 'Mentioned',
  },
  {
    year: 2018,
    name: 'Morales v. Sunbelt Rental Group',
    description: 'Forklift and pickup collision in a rental yard.',
    court: 'D. Ariz.',
    side: 'defense',
    involvement: 'Deposition',
  },
]

export const DEMO_PROFILES: Record<string, ExpertProfile> = {
  'demo-1': {
    name: 'Dr. Margaret Chen',
    initials: 'MC',
    title: 'Expert witness in orthopedic surgery, spine injury and surgical standard of care',
    contact: [
      { label: 'Based in', value: 'Columbus, Ohio' },
      { label: 'Business address', value: '1180 Larkspur Ave, Suite 300, Columbus, OH 43215' },
      { label: 'Currently', value: 'Attending Orthopedic Surgeon at Riverside Orthopedic Group' },
      { label: 'Phone', value: '614-555-0142' },
      { label: 'Website', value: 'www.mchen-ortho.com' },
    ],
    tags: [
      'Spine surgery',
      'Joint replacement',
      'Surgical standard of care',
      'Injury causation',
      'Independent medical exams',
      'Hardware failure',
    ],
    qualifications:
      'Dr. Margaret Chen is a board-certified orthopedic surgeon with 15 years in practice, specializing in spine surgery and joint replacement. She served as Chief of Orthopedics at Mid-Ohio Regional Medical Center from 2015 to 2020 and is a clinical associate professor at Central Ohio College of Medicine. She has reviewed over 120 cases as an expert, roughly 60% for the defense, and has testified at trial four times. Her opinions focus on surgical standard of care, causation of spinal and joint injuries after trauma, and post-operative hardware failure.',
    highlights: [
      { label: 'In practice', value: '15 years' },
      { label: 'Board certification', value: 'American Board of Orthopaedic Surgery' },
      { label: 'Cases reviewed', value: '120+, about 60% for the defense' },
      { label: 'Trial testimony', value: '4 times' },
      { label: 'Surgical volume', value: 'About 150 spine fusions a year, operates 3 days a week' },
    ],
    cases: CHEN_CASES,
    daubertChallenges: 0,
    documents: [
      {
        label: 'Deposition transcripts',
        value: '3 found',
        details: transcripts(CHEN_CASES, 'Deposition'),
      },
      {
        label: 'Trial transcripts',
        value: '2 found',
        details: transcripts(CHEN_CASES, 'Trial testimony'),
      },
      {
        label: 'Court exhibits & filings',
        value: '4 found',
        details: [
          {
            title: 'Expert report · Harmon v. Mid-Ohio Regional Medical Center',
            detail: 'Filed 2025 · defense exhibit D-14',
          },
          {
            title: 'Supplemental report · Whitfield v. Grandview Surgical Associates',
            detail: 'Filed 2023 · trial exhibit 31',
          },
          {
            title: 'Curriculum vitae · Delgado v. Kessler Freight Lines',
            detail: 'Attached to the expert disclosure, 2024',
          },
          {
            title: 'Fee schedule · Pruitt v. Summit Orthopedics, Inc.',
            detail: 'Produced in discovery, 2019',
          },
        ],
      },
      {
        label: 'Affidavits',
        value: '1 found',
        details: [
          {
            title: 'Affidavit of merit · Okoro v. Lakeshore Mutual Insurance Co.',
            detail: 'Sworn 2022 · supports her causation opinion on the rotator cuff tear',
          },
        ],
      },
      {
        label: 'Personal litigation',
        value: '1 found · dismissed 2017',
        flag: true,
        details: [
          {
            title: 'Chen v. Brightway Property Management',
            detail:
              'Filed 2016 · Franklin Cnty. C.P. · dispute over her practice office lease, brought by Dr. Chen. Dismissed by agreement in 2017.',
          },
        ],
      },
      { label: 'Expert challenges', value: 'None found' },
    ],
    risk: [
      { label: 'Disciplinary actions', value: 'None found' },
      { label: 'Board sanctions', value: 'None found' },
      background('Ohio medical license active, in good standing'),
      { label: 'Daubert / Frye challenges', value: 'None found' },
    ],
    publications: [
      {
        title: 'Outcomes of revision lumbar fusion in patients over 65',
        detail: 'Peer-reviewed spine journal, 2021',
      },
      {
        title: 'Hardware failure after posterior fusion: a 10-year review',
        detail: 'Ohio Orthopaedic Society, annual meeting 2019',
      },
      {
        title: 'The orthopedic expert in trauma litigation',
        detail: 'Lecture, Columbus Bar Association CLE, 2023',
      },
    ],
    news: [
      {
        title: '“Local surgeon named chief of orthopedics”',
        detail: 'Regional news feature, 2015',
      },
      {
        title: 'Quoted on recovery after spinal fusion',
        detail: 'Health column, regional daily paper, 2021',
      },
    ],
    social: [
      { title: 'LinkedIn', detail: 'Active · posts about her practice and CLE talks' },
      { title: 'X (Twitter)', detail: 'Inactive since 2020 · no case-related posts found' },
    ],
    online: [
      { title: 'Practice website and CV', detail: 'mchen-ortho.com' },
      { title: 'Physician directory profile', detail: 'Doximity' },
      { title: 'Expert directory listing', detail: 'ExpertPages' },
    ],
    career: [
      {
        title: 'Attending Orthopedic Surgeon',
        org: 'Riverside Orthopedic Group',
        dates: '2012–present',
        current: true,
      },
      {
        title: 'Clinical Associate Professor',
        org: 'Central Ohio College of Medicine',
        dates: '2016–present',
        current: true,
      },
      {
        title: 'Chief of Orthopedics',
        org: 'Mid-Ohio Regional Medical Center',
        dates: '2015–2020',
      },
      { title: 'Spine Surgery Fellow', org: 'Northfield University Hospital', dates: '2010–2011' },
      {
        title: 'Orthopedic Surgery Resident',
        org: 'Northfield University Hospital',
        dates: '2005–2010',
      },
    ],
    education: [
      { title: 'Fellowship, Spine Surgery', detail: 'Northfield University Hospital' },
      { title: 'Residency, Orthopedic Surgery', detail: 'Northfield University Hospital' },
      { title: 'MD', detail: 'Lakeview University School of Medicine' },
      { title: 'BS, Biology', detail: 'Ashford College' },
    ],
    professional: [
      {
        label: 'Licenses & certifications',
        value: 'Ohio medical license · Board certified, American Board of Orthopaedic Surgery',
      },
      {
        label: 'Hospital affiliations',
        value: 'Mid-Ohio Regional Medical Center, Riverside Surgical Hospital',
      },
      {
        label: 'Memberships & associations',
        value: 'American Academy of Orthopaedic Surgeons, North American Spine Society',
      },
      {
        label: 'Expert fees',
        value: '$650/hr record review · $4,500 half-day deposition · $9,000 per trial day',
      },
    ],
    sources: [
      'Practice website · CV',
      'ExpertPages profile',
      'State Medical Board license lookup',
      'CourtListener',
      'PubMed',
    ],
  },

  'demo-2': {
    name: 'Dr. Robert Alvarez',
    initials: 'RA',
    title: 'Expert witness in accident reconstruction, vehicle dynamics and trucking collisions',
    contact: [
      { label: 'Based in', value: 'Phoenix, Arizona' },
      { label: 'Business address', value: '4410 E Saguaro Vista Dr, Suite 210, Phoenix, AZ 85016' },
      { label: 'Currently', value: 'Principal Engineer at Alvarez Collision Analysis, LLC' },
      { label: 'Phone', value: '602-555-0187' },
      { label: 'Website', value: 'www.alvarezcollision.com' },
    ],
    tags: [
      'Accident reconstruction',
      'Commercial trucking',
      'Event data recorders',
      'Vehicle dynamics',
      'Pedestrian collisions',
      'Human factors',
    ],
    qualifications:
      'Dr. Robert Alvarez is a licensed professional engineer with a PhD in mechanical engineering and 22 years of collision reconstruction experience. Before founding Alvarez Collision Analysis in 2011 he led the crash investigation unit of a state highway patrol. He reconstructs passenger-vehicle, commercial-truck and pedestrian collisions using event data recorder downloads, 3D scene scans and simulation. He reports work for both plaintiffs and defendants and has testified in state and federal courts in Arizona, Nevada, New Mexico and Texas.',
    highlights: [
      { label: 'Experience', value: '22 years in collision reconstruction' },
      { label: 'Licensed', value: 'Professional Engineer, AZ and NV' },
      { label: 'Accreditation', value: 'ACTAR accredited' },
      { label: 'Has testified in', value: 'AZ, NV, NM and TX courts' },
      { label: 'Work split (per CV)', value: 'About 50% plaintiff, 50% defense' },
    ],
    cases: ALVAREZ_CASES,
    daubertChallenges: 1,
    documents: [
      {
        label: 'Deposition transcripts',
        value: '5 found',
        details: transcripts(ALVAREZ_CASES, 'Deposition'),
      },
      {
        label: 'Trial transcripts',
        value: '2 found',
        details: transcripts(ALVAREZ_CASES, 'Trial testimony'),
      },
      {
        label: 'Court exhibits & filings',
        value: '4 found',
        details: [
          {
            title: 'Reconstruction report · Nguyen v. Desert Sun Transport, LLC',
            detail: 'Filed 2026 · plaintiff exhibit P-22',
          },
          {
            title: '3D scene animation · Saguaro Mutual Insurance v. Ortega',
            detail: 'Demonstrative trial exhibit, 2025',
          },
          {
            title: 'Rebuttal report · Reyes v. Copperline Logistics',
            detail: 'Filed 2022',
          },
          {
            title: 'EDR download summary · Lindqvist v. Rapid Haul Co.',
            detail: 'Trial exhibit 9, 2021',
          },
        ],
      },
      {
        label: 'Affidavits',
        value: '3 found',
        details: [
          {
            title: 'Declaration on speed analysis · Reyes v. Copperline Logistics',
            detail: '2022 · filed opposing the motion to exclude',
          },
          {
            title: 'Affidavit · Campos v. Westline Freight, Inc.',
            detail: '2024 · supports the opposition to summary judgment',
          },
          {
            title: 'Affidavit · Morales v. Sunbelt Rental Group',
            detail: '2018 · authenticates his site inspection photos',
          },
        ],
      },
      { label: 'Personal litigation', value: 'None found' },
      {
        label: 'Expert challenges',
        value: '1 found',
        flag: true,
        details: [
          {
            title: 'Reyes v. Copperline Logistics (D.N.M. 2022)',
            detail:
              'Motion to exclude his opinions. Granted in part: the speed estimate was excluded as resting on unreliable crush data; his braking and sightline opinions were admitted.',
          },
        ],
      },
    ],
    risk: [
      { label: 'Disciplinary actions', value: 'None found' },
      { label: 'Board sanctions', value: 'None found' },
      background('Arizona and Nevada PE licenses active, in good standing'),
      {
        label: 'Daubert / Frye challenges',
        value: '1 · partially excluded',
        flag: true,
        details: [
          {
            title: 'Reyes v. Copperline Logistics (D.N.M. 2022)',
            detail:
              'Motion to exclude his opinions. Granted in part: the speed estimate was excluded as resting on unreliable crush data; his braking and sightline opinions were admitted.',
          },
        ],
      },
    ],
    publications: [
      {
        title: 'Reconciling EDR pre-crash data with scene evidence',
        detail: 'Technical paper, 2020',
      },
      {
        title: 'Night-time conspicuity of stopped trailers',
        detail: 'Peer-reviewed reconstruction journal, 2017',
      },
      {
        title: 'Reading the black box: EDRs for trial lawyers',
        detail: 'Lecture, regional trial lawyers seminar, 2024',
      },
    ],
    news: [
      {
        title: '“How investigators rebuild a highway crash”',
        detail: 'Regional news feature, 2022',
      },
      {
        title: 'Interviewed on truck underride guards',
        detail: 'Local TV news segment, 2024',
      },
    ],
    social: [
      { title: 'LinkedIn', detail: 'Active · posts reconstruction case studies' },
      {
        title: 'YouTube',
        detail: '6 educational crash-analysis videos · no case-specific content',
      },
    ],
    online: [
      { title: 'Firm website and CV', detail: 'alvarezcollision.com' },
      { title: 'Expert directory listing', detail: 'National expert witness directory' },
    ],
    career: [
      {
        title: 'Principal Engineer',
        org: 'Alvarez Collision Analysis, LLC',
        dates: '2011–present',
        current: true,
      },
      {
        title: 'Adjunct Professor, Mechanical Engineering',
        org: 'Sonoran State University',
        dates: '2014–present',
        current: true,
      },
      {
        title: 'Commander, Crash Investigation Unit',
        org: 'State Highway Patrol',
        dates: '2006–2011',
      },
      { title: 'Collision Investigator', org: 'State Highway Patrol', dates: '2001–2006' },
    ],
    education: [
      { title: 'PhD, Mechanical Engineering', detail: 'Sonoran State University' },
      { title: 'MS, Mechanical Engineering', detail: 'Sonoran State University' },
      { title: 'BS, Civil Engineering', detail: 'Mesa Polytechnic Institute' },
      { title: 'Accredited Traffic Accident Reconstructionist', detail: 'ACTAR' },
    ],
    professional: [
      { label: 'Licenses & certifications', value: 'Professional Engineer (AZ, NV) · ACTAR accredited' },
      { label: 'Affiliations', value: 'Sonoran State University' },
      {
        label: 'Memberships & associations',
        value:
          'SAE International, National Association of Professional Accident Reconstruction Specialists',
      },
      {
        label: 'Expert fees',
        value: '$425/hr · $600/hr deposition (4-hr minimum) · $3,400 per trial day',
      },
    ],
    sources: ['Firm website · CV', 'State engineering board lookup', 'CourtListener', 'PACER'],
  },
}
