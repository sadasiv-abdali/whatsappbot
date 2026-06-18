import { useState } from 'react'
import './App.css'
import ProjectForm from './components/ProjectForm'

function App() {
  return (
    <div className="app-container">
      <header className="app-header">
        <h1>AI Scene Video Factory</h1>
      </header>

      <div className="app-main">
        <aside className="app-sidebar">
          <nav>
            <ul>
              <li style={{listStyle: 'none', margin: '0 0 1rem 0'}}>
                <a href="#" style={{color: 'var(--text-primary)', textDecoration: 'none', fontWeight: 'bold'}}>New Project</a>
              </li>
              <li style={{listStyle: 'none', margin: '0 0 1rem 0'}}>
                <a href="#" style={{color: 'var(--text-secondary)', textDecoration: 'none'}}>Dashboard</a>
              </li>
            </ul>
          </nav>
        </aside>

        <main className="app-content">
          <div className="glass-panel">
            <h2>Create New Project</h2>
            <p style={{color: 'var(--text-secondary)', marginBottom: '2rem'}}>
              Fill out the details below to start a new automated video generation project.
            </p>
            <ProjectForm />
          </div>
        </main>
      </div>
    </div>
  )
}

export default App
