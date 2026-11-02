// The user's cases, mocked. Swap for the real case list once the backend has one.
import { Case } from '@/lib/types'

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
