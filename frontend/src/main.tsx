import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './index.css'
import ExpertSearchPage from './pages/ExpertSearchPage'
import ExpertProfilePage from './pages/ExpertProfilePage'
import CaseContextPage from './pages/CaseContextPage'
import ExpertResearchLive from './pages/ExpertResearchLive'
import SearchesPage from './pages/SearchesPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ExpertSearchPage />} />
        <Route path="/experts/:expertId" element={<ExpertProfilePage />} />
        <Route path="/experts/:expertId/context" element={<CaseContextPage />} />
        <Route path="/research/:dossierId" element={<ExpertResearchLive />} />
        <Route path="/searches" element={<SearchesPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>
)
