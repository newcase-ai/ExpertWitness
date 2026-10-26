// Canned "Investigate" answers for the mock expert profiles. Keyed by
// the same demo ids as DEMO_PROFILES. A highlight target is `${sectionId}:${item label}`, where the
// label is the case name, fact label, entry title or career role title as shown in the profile.

export type InvestigationTopic = 'Credibility' | 'Testimony' | 'Fees & practice'

export interface Investigation {
  question: string
  topic: InvestigationTopic
  /** Free-typed questions match the investigation with the most of these (lowercase) in them. */
  keywords: string[]
  steps: string[]
  answer: string // markdown
  sources: string[]
  highlights: { target: string; note: string }[]
}

export const INVESTIGATIONS: Record<string, Investigation[]> = {
  'demo-1': [
    {
      question: 'Where has she contradicted herself across her testimony?',
      topic: 'Credibility',
      keywords: ['contradict', 'inconsisten', 'flip', 'changed her'],
      steps: [
        'Reading 3 deposition transcripts',
        'Reading 2 trial transcripts',
        'Comparing her statements on degenerative disc disease',
        'Checking which side retained her in each case',
      ],
      answer:
        '**One clear inconsistency: whether degenerative disc disease explains a spine injury.** Her position lines up with the side that retained her.\n\n- **Delgado v. Kessler Freight Lines** (2024 deposition, for plaintiff): “Degenerative changes on the MRI do not rule out an acute traumatic herniation.”\n- **Pruitt v. Summit Orthopedics** (2019 trial, for defense): pre-existing degeneration was “the most likely cause” of the patient’s symptoms.\n\nNo other conflicts found across her remaining 4 cases.',
      sources: ['Deposition · Delgado, 2024', 'Trial · Pruitt, 2019'],
      highlights: [
        {
          target: 'litigation:Delgado v. Kessler Freight Lines',
          note: 'Said degeneration does not rule out an acute traumatic herniation',
        },
        {
          target: 'litigation:Pruitt v. Summit Orthopedics, Inc.',
          note: 'Said pre-existing degeneration was the most likely cause',
        },
      ],
    },
    {
      question: 'Has she ever been disqualified, or had her testimony excluded?',
      topic: 'Credibility',
      keywords: ['disqualif', 'exclud', 'stricken', 'strike', 'daubert', 'frye', 'precluded'],
      steps: [
        'Searching 6 cases in her litigation record',
        'Checking Daubert / Frye rulings',
        'Reading 4 court exhibits & filings',
      ],
      answer:
        '**No.** I found no ruling that disqualified Dr. Chen or excluded or limited her testimony.\n\n- 0 Daubert or Frye challenges across her 6 cases.\n- Both trials where she testified (*Whitfield*, 2023 and *Pruitt*, 2019) admitted her opinions in full.\n- No motion to strike her appears in the filings found.',
      sources: ['Records · Expert challenges', 'Risk · Daubert / Frye'],
      highlights: [
        { target: 'litigation:Expert challenges', note: 'None found across 6 cases' },
        { target: 'risk:Daubert / Frye challenges', note: 'None found' },
        {
          target: 'litigation:Whitfield v. Grandview Surgical Associates',
          note: 'Testified at trial; opinions admitted in full',
        },
      ],
    },
    {
      question: 'What does she charge for record review, depositions and trial?',
      topic: 'Fees & practice',
      keywords: ['charge', 'fee', 'rate', 'cost', 'bill', 'hourly'],
      steps: [
        'Reading her fee schedule from Pruitt v. Summit Orthopedics',
        'Checking the rates on her ExpertPages listing',
        'Comparing with the rates she gave in the Harmon deposition',
      ],
      answer:
        '**Her current rates:**\n\n- **Record review:** $650 an hour\n- **Deposition:** $4,500 for a half day\n- **Trial testimony:** $9,000 per day\n\nThe 2019 fee schedule produced in *Pruitt* showed $500 an hour for review, so her rate has gone up about 30% since then.',
      sources: ['Professional · Expert fees', 'Exhibit · Fee schedule, Pruitt 2019'],
      highlights: [
        { target: 'professional:Expert fees', note: 'Her current rates' },
        {
          target: 'litigation:Court exhibits & filings',
          note: 'Includes her 2019 fee schedule from Pruitt',
        },
      ],
    },
    {
      question: 'What percentage of her work is for plaintiffs vs. the defense?',
      topic: 'Fees & practice',
      keywords: ['plaintiff', 'defense', 'defendant', 'split', 'percentage', 'side'],
      steps: [
        'Counting which side retained her in 6 cases',
        'Reading her own estimate from the Harmon deposition',
        'Comparing the two',
      ],
      answer:
        '**Mostly defense.** She says about 60% of her 120+ reviews are for the defense.\n\nThe cases found in public records match that: **3 for the defense, 2 for plaintiffs, 1 unknown**. Both of her trial appearances were for the defense.',
      sources: ['Qualifications · Cases reviewed', 'Litigation · 6 cases'],
      highlights: [
        { target: 'qualifications:Cases reviewed', note: 'Her own estimate: about 60% defense' },
        { target: 'litigation:Harmon v. Mid-Ohio Regional Medical Center', note: 'Defense' },
        {
          target: 'litigation:Whitfield v. Grandview Surgical Associates',
          note: 'Defense, at trial',
        },
        { target: 'litigation:Pruitt v. Summit Orthopedics, Inc.', note: 'Defense, at trial' },
      ],
    },
    {
      question: 'Does she belong to an expert witness brokerage or directory service?',
      topic: 'Fees & practice',
      keywords: [
        'brokerage',
        'broker',
        'seak',
        'expert institute',
        'service',
        'advertis',
        'directory',
      ],
      steps: [
        'Searching expert directories and brokerage listings',
        'Checking her practice website',
        'Cross-checking the sources this profile was compiled from',
      ],
      answer:
        '**Yes, she is listed with one expert witness directory.**\n\n- **ExpertPages**: an active listing for orthopedic surgery and spine injury.\n- No listing found with the Expert Institute or other brokerage services.\n\nHer practice website does not advertise expert work.',
      sources: ['Online · ExpertPages listing'],
      highlights: [
        { target: 'risk:Expert directory listing', note: 'Active ExpertPages listing' },
        { target: 'risk:Practice website and CV', note: 'Does not mention expert work' },
      ],
    },
    {
      question:
        'Has she testified that complications can happen even when the standard of care is met?',
      topic: 'Testimony',
      keywords: ['complication', 'standard of care', 'negligen', 'despite', 'appropriate care'],
      steps: [
        'Reading the Harmon v. Mid-Ohio Regional Medical Center deposition',
        'Reading the Pruitt and Whitfield trial transcripts',
        'Looking for statements on the known risks of surgery',
      ],
      answer:
        '**Yes, more than once, and always for the defense.**\n\n- **Harmon** (2025 deposition): a failed lumbar fusion “can and does happen with technically perfect surgery.”\n- **Pruitt** (2019 trial): hardware failure is “a recognized risk the surgeon cannot fully eliminate.”\n- **Whitfield** (2023 trial): hip dislocation after replacement “is not by itself evidence of negligence.”\n\nUseful if she is opposing you on a plaintiff case.',
      sources: ['Deposition · Harmon, 2025', 'Trial · Pruitt, 2019', 'Trial · Whitfield, 2023'],
      highlights: [
        {
          target: 'litigation:Harmon v. Mid-Ohio Regional Medical Center',
          note: 'Failed fusion can happen with technically perfect surgery',
        },
        {
          target: 'litigation:Whitfield v. Grandview Surgical Associates',
          note: 'Dislocation is not by itself evidence of negligence',
        },
        {
          target: 'litigation:Pruitt v. Summit Orthopedics, Inc.',
          note: 'Hardware failure is a recognized, unavoidable risk',
        },
      ],
    },
    {
      question: 'Is there any textbook or literature she considers authoritative?',
      topic: 'Testimony',
      keywords: ['literature', 'textbook', 'authoritative', 'guideline', 'journal', 'reliable'],
      steps: [
        'Searching trial transcripts for questions on authority',
        'Reading her 3 publications and lectures',
        'Checking which guidelines she cites',
      ],
      answer:
        '**She relies on the AAOS clinical practice guidelines** and declined to call any single textbook authoritative.\n\n- **Whitfield** (2023 trial): named the AAOS guidelines as “the standard I hold myself to.”\n- She often cites her own paper, *Outcomes of revision lumbar fusion in patients over 65* (2021).\n\nExpect her to resist being pinned to a specific textbook.',
      sources: ['Trial · Whitfield, 2023', 'Publication · 2021'],
      highlights: [
        {
          target: 'litigation:Whitfield v. Grandview Surgical Associates',
          note: 'Named the AAOS guidelines as authoritative',
        },
        {
          target: 'risk:Outcomes of revision lumbar fusion in patients over 65',
          note: 'Her own paper, which she often cites',
        },
      ],
    },
    {
      question: 'Has she faced license discipline or given up hospital privileges?',
      topic: 'Credibility',
      keywords: ['disciplin', 'license', 'privilege', 'sanction', 'board'],
      steps: [
        'Checking the State Medical Board license lookup',
        'Searching board sanctions and disciplinary records',
        'Reviewing her hospital appointments',
      ],
      answer:
        '**No discipline found.** Her Ohio medical license is active and in good standing.\n\n- No disciplinary actions or board sanctions.\n- No hospital privileges given up or revoked. She still holds privileges at Mid-Ohio Regional and Riverside Surgical Hospital.\n- She left the Chief of Orthopedics post in 2020 at the end of her term and kept her privileges.',
      sources: ['Risk · Disciplinary actions', 'Professional · Hospital affiliations'],
      highlights: [
        { target: 'risk:Disciplinary actions', note: 'None found' },
        { target: 'risk:Board sanctions', note: 'None found' },
        { target: 'professional:Hospital affiliations', note: 'Privileges still held at both' },
        { target: 'professional:Chief of Orthopedics', note: 'Left at the end of her term' },
      ],
    },
    {
      question: 'Has she ever been sued, for malpractice or otherwise?',
      topic: 'Credibility',
      keywords: ['sued', 'malpractice', 'lawsuit', 'personal', 'party to'],
      steps: [
        'Searching court records for cases naming her as a party',
        'Checking malpractice claim records',
        'Reading the 2016 lease case',
      ],
      answer:
        '**No malpractice suits found.** She has been a party to one lawsuit, and it was not about patient care.\n\n- **Chen v. Brightway Property Management** (2016, Franklin Cnty. C.P.): she sued her landlord over her practice office lease. Dismissed by agreement in 2017.',
      sources: ['Records · Personal litigation'],
      highlights: [
        { target: 'litigation:Personal litigation', note: 'Office lease dispute, dismissed 2017' },
      ],
    },
    {
      question: 'How many spine surgeries does she perform a year, and is she still operating?',
      topic: 'Fees & practice',
      keywords: ['surger', 'operat', 'fusion', 'clinical', 'retire', 'still'],
      steps: [
        'Reading the Harmon deposition on her current practice',
        'Checking her hospital affiliations',
        'Comparing her clinical and expert workload',
      ],
      answer:
        '**She is still a full-time surgeon.** In the *Harmon* deposition (2025) she said she:\n\n- performs about **150 spine fusions a year**\n- operates **3 days a week** at Riverside Surgical Hospital\n- spends about **10 hours a week** on expert work\n\nThat makes her hard to attack as a professional witness.',
      sources: ['Deposition · Harmon, 2025', 'Qualifications · Surgical volume'],
      highlights: [
        { target: 'qualifications:Surgical volume', note: 'From her 2025 deposition' },
        {
          target: 'litigation:Harmon v. Mid-Ohio Regional Medical Center',
          note: 'Where she described her current practice',
        },
      ],
    },
  ],

  'demo-2': [
    {
      question: 'Where has he contradicted himself across his testimony?',
      topic: 'Credibility',
      keywords: ['contradict', 'inconsisten', 'flip', 'changed his'],
      steps: [
        'Reading 5 deposition transcripts',
        'Reading 2 trial transcripts',
        'Comparing his statements on crush-based speed estimates',
        'Checking which side retained him in each case',
      ],
      answer:
        '**One clear inconsistency: whether crush-based speed estimates are reliable.**\n\n- **Reyes v. Copperline Logistics** (2022 deposition, for defense): defended his speed estimate built from vehicle crush as “a reliable, accepted method.”\n- **Campos v. Westline Freight** (2024 deposition, for plaintiff): said crush-based estimates are “too uncertain to support a speed opinion” when he attacked the defense expert.\n\nThe *Reyes* court later excluded his crush-based speed opinion.',
      sources: ['Deposition · Reyes, 2022', 'Deposition · Campos, 2024'],
      highlights: [
        {
          target: 'litigation:Reyes v. Copperline Logistics',
          note: 'Called crush-based speed estimates reliable',
        },
        {
          target: 'litigation:Campos v. Westline Freight, Inc.',
          note: 'Called crush-based estimates too uncertain',
        },
      ],
    },
    {
      question: 'Has he ever been disqualified, or had his testimony excluded?',
      topic: 'Credibility',
      keywords: ['disqualif', 'exclud', 'stricken', 'strike', 'daubert', 'frye', 'precluded'],
      steps: [
        'Searching 8 cases in his litigation record',
        'Checking Daubert / Frye rulings',
        'Reading the Reyes order on the motion to exclude',
      ],
      answer:
        '**Yes, partially, once.**\n\n- **Reyes v. Copperline Logistics** (D.N.M. 2022): the plaintiff moved to exclude his opinions. The court excluded his speed estimate as resting on unreliable crush data, and admitted his braking and sightline opinions.\n\nNo other challenges found across his 8 cases, and he has never been fully disqualified.',
      sources: ['Records · Expert challenges', 'Risk · Daubert / Frye'],
      highlights: [
        { target: 'litigation:Reyes v. Copperline Logistics', note: 'Speed opinion excluded' },
        { target: 'litigation:Expert challenges', note: 'The one challenge found' },
        { target: 'risk:Daubert / Frye challenges', note: 'Partially excluded' },
      ],
    },
    {
      question: 'What does he charge for his work, depositions and trial?',
      topic: 'Fees & practice',
      keywords: ['charge', 'fee', 'rate', 'cost', 'bill', 'hourly'],
      steps: [
        'Reading his firm’s published fee schedule',
        'Checking the rates on his directory listing',
        'Comparing with rates in filed expert disclosures',
      ],
      answer:
        '**His current rates:**\n\n- **Reconstruction work and reports:** $425 an hour\n- **Deposition:** $600 an hour, 4-hour minimum\n- **Trial testimony:** $3,400 per day\n\nThese match his firm’s fee schedule and his directory listing.',
      sources: ['Professional · Expert fees', 'Online · Directory listing'],
      highlights: [
        { target: 'professional:Expert fees', note: 'His current rates' },
        { target: 'risk:Expert directory listing', note: 'Same rates listed here' },
      ],
    },
    {
      question: 'Does he testify for both plaintiffs and defendants, and what’s the split?',
      topic: 'Fees & practice',
      keywords: ['plaintiff', 'defense', 'defendant', 'split', 'percentage', 'side', 'both'],
      steps: [
        'Counting which side retained him in 8 cases',
        'Reading the work split stated on his CV',
        'Comparing the two',
      ],
      answer:
        '**Close to even, leaning defense.** His CV says he works about 50/50.\n\nThe 8 cases found show **4 for the defense, 3 for plaintiffs and 1 unknown** (a criminal case where he wrote the prosecution’s report). His 2 trial appearances split one each way.',
      sources: ['Qualifications · Work split', 'Litigation · 8 cases'],
      highlights: [
        { target: 'qualifications:Work split (per CV)', note: 'His claim: about 50/50' },
        { target: 'litigation:Saguaro Mutual Insurance v. Ortega', note: 'Trial, for the defense' },
        { target: 'litigation:Lindqvist v. Rapid Haul Co.', note: 'Trial, for the plaintiff' },
      ],
    },
    {
      question: 'Does he belong to an expert witness brokerage or directory service?',
      topic: 'Fees & practice',
      keywords: [
        'brokerage',
        'broker',
        'seak',
        'expert institute',
        'service',
        'advertis',
        'directory',
      ],
      steps: [
        'Searching expert directories and brokerage listings',
        'Checking his firm website',
        'Cross-checking the sources this profile was compiled from',
      ],
      answer:
        '**Yes.** He has an active listing in a **national expert witness directory**, advertising accident reconstruction and trucking work.\n\nHis firm website also markets litigation services directly.',
      sources: ['Online · Directory listing', 'Online · Firm website'],
      highlights: [
        { target: 'risk:Expert directory listing', note: 'Active directory listing' },
        { target: 'risk:Firm website and CV', note: 'Markets litigation services' },
      ],
    },
    {
      question: 'In which states has he testified at trial or by deposition?',
      topic: 'Testimony',
      keywords: ['state', 'where', 'jurisdiction', 'court'],
      steps: [
        'Mapping the court for each of his 8 cases',
        'Separating trial testimony from depositions',
      ],
      answer:
        '**4 states: Arizona, Nevada, New Mexico and Texas.**\n\n- **Arizona:** 5 cases, in federal and state court\n- **Nevada:** *Campos* (D. Nev.)\n- **New Mexico:** *Reyes* (D.N.M.)\n- **Texas:** *Lindqvist* (W.D. Tex.)\n\nHis trial testimony was in Arizona and Texas.',
      sources: ['Qualifications · Has testified in', 'Litigation · 8 cases'],
      highlights: [
        { target: 'qualifications:Has testified in', note: 'AZ, NV, NM and TX' },
        { target: 'litigation:Campos v. Westline Freight, Inc.', note: 'Nevada' },
        { target: 'litigation:Reyes v. Copperline Logistics', note: 'New Mexico' },
        { target: 'litigation:Lindqvist v. Rapid Haul Co.', note: 'Texas, at trial' },
      ],
    },
    {
      question: 'How does he handle EDR data that conflicts with the scene evidence?',
      topic: 'Testimony',
      keywords: ['edr', 'black box', 'event data', 'scene'],
      steps: [
        'Reading his 2020 paper on EDR data',
        'Reading the Lindqvist trial transcript',
        'Comparing with his Campos deposition',
      ],
      answer:
        '**He treats the EDR as one input and checks it against the physical evidence.**\n\n- In his 2020 paper *Reconciling EDR pre-crash data with scene evidence* he argues EDR speed can be off by up to 10% after wheel slip.\n- **Lindqvist v. Rapid Haul** (2021 trial): he adjusted the EDR speed using tire marks and a 3D scene scan.\n\nOpposing counsel could contrast this with *Campos*, where he treated the EDR as decisive.',
      sources: ['Publication · 2020', 'Trial · Lindqvist, 2021'],
      highlights: [
        {
          target: 'risk:Reconciling EDR pre-crash data with scene evidence',
          note: 'EDR speed can be off by up to 10%',
        },
        {
          target: 'litigation:Lindqvist v. Rapid Haul Co.',
          note: 'Adjusted the EDR speed using tire marks',
        },
      ],
    },
    {
      question: 'What literature does he consider authoritative?',
      topic: 'Testimony',
      keywords: ['literature', 'textbook', 'authoritative', 'journal', 'reliable', 'reference'],
      steps: [
        'Searching trial transcripts for questions on authority',
        'Reading his 3 publications and lectures',
      ],
      answer:
        '**SAE technical papers**, which he called “the standard references in our field” at the *Saguaro* trial (2025).\n\nHe also relies on his own 2017 paper on night-time trailer conspicuity, which he cited in *Campos*.',
      sources: ['Trial · Saguaro, 2025', 'Publication · 2017'],
      highlights: [
        {
          target: 'litigation:Saguaro Mutual Insurance v. Ortega',
          note: 'Called SAE papers the standard references',
        },
        {
          target: 'risk:Night-time conspicuity of stopped trailers',
          note: 'His own paper, cited in Campos',
        },
      ],
    },
    {
      question: 'What perception-reaction time does he use for drivers?',
      topic: 'Testimony',
      keywords: ['perception', 'reaction', 'prt'],
      steps: [
        'Searching his testimony for perception-reaction times',
        'Reading the Hale and Morales depositions',
        'Comparing the values he used',
      ],
      answer:
        '**He has used two different values.**\n\n- **Hale v. City of Tucson** (2023, for defense): 1.5 seconds for the driver who struck a pedestrian at night.\n- **Morales v. Sunbelt Rental Group** (2018, for defense): 1.0 second in a daylight yard collision.\n\nHe explained the gap by lighting and how expected the hazard was. Worth pressing if your case turns on reaction time.',
      sources: ['Deposition · Hale, 2023', 'Deposition · Morales, 2018'],
      highlights: [
        { target: 'litigation:Hale v. City of Tucson', note: 'Used 1.5 seconds' },
        { target: 'litigation:Morales v. Sunbelt Rental Group', note: 'Used 1.0 second' },
      ],
    },
    {
      question: 'Why did he leave the State Highway Patrol, and was he ever disciplined?',
      topic: 'Credibility',
      keywords: ['patrol', 'police', 'law enforcement', 'leave', 'left', 'disciplin'],
      steps: [
        'Reading his career history',
        'Searching public records for internal discipline',
        'Checking the state engineering board',
      ],
      answer:
        '**He left in 2011 to found Alvarez Collision Analysis**, after 10 years with the State Highway Patrol, the last 5 as commander of its crash investigation unit.\n\n- No internal discipline or complaints found in public records.\n- His Professional Engineer licenses in Arizona and Nevada are active, with no board actions.',
      sources: ['Career · State Highway Patrol', 'Risk · Board sanctions'],
      highlights: [
        { target: 'professional:Commander, Crash Investigation Unit', note: 'Left in 2011' },
        { target: 'professional:Principal Engineer', note: 'Founded his firm in 2011' },
        { target: 'risk:Disciplinary actions', note: 'None found' },
        { target: 'risk:Board sanctions', note: 'None found' },
      ],
    },
  ],
}

export function findInvestigation(demoId: string, text: string): Investigation | undefined {
  const q = text.toLowerCase()
  const exact = INVESTIGATIONS[demoId]?.find((i) => i.question.toLowerCase() === q)
  if (exact) return exact
  let best: Investigation | undefined
  let bestScore = 0
  for (const i of INVESTIGATIONS[demoId] ?? []) {
    const score = i.keywords.filter((k) => q.includes(k)).length
    if (score > bestScore) [best, bestScore] = [i, score]
  }
  return best
}
