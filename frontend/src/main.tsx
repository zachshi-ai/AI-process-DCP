import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

if (window.__AI_DCP__?.setApiBase && window.__AI_DCP_API_BASE) {
  window.__AI_DCP__.setApiBase(window.__AI_DCP_API_BASE)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
