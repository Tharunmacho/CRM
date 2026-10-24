import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './app/App'
import { initTheme } from './shared/lib/theme'
import './styles/fonts.css'
import './styles/theme.css'
import './styles/index.css'
import './styles/dashboard.css'
import './styles/controls.css'
import './styles/workspace.css'
import './styles/dashboard-refresh.css'
import './styles/crm-theme.css'
import './styles/compact.css'
import './styles/depth.css'

initTheme()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
