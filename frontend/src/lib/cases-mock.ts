// The user's cases, mocked. Swap for the real case list once the backend has one.
export interface Case {
  id: string
  name: string
  type: string
  court: string
  client: 'plaintiff' | 'defendant' // the side the user's firm represents
}

export const CASES: Case[] = [
  {
    id: 'reyes',
    name: 'Reyes v. Mid-Ohio Regional Medical Center',
    type: 'Medical malpractice',
    court: 'Franklin County, OH',
    client: 'plaintiff',
  },
  {
    id: 'brennan',
    name: 'Brennan v. Lakeshore Orthopedic Associates',
    type: 'Medical malpractice',
    court: 'Cook County, IL',
    client: 'defendant',
  },
  {
    id: 'hale',
    name: 'Hale v. City of Tucson',
    type: 'Personal injury',
    court: 'Pima County, AZ',
    client: 'plaintiff',
  },
]

export type ExpertRole = 'opposing' | 'ours'
