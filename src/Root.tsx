import { useEffect, useLayoutEffect, useState } from 'react'
import App from './App'
import AuthPage from './components/AuthPage'
import { clearToken, getCurrentUser, readToken, saveToken, type UserInfo } from './api'
import { readFontSize, saveFontSize } from './utils/fontSize'

function currentRoute() { return window.location.hash.replace(/^#/, '') || '/' }

export default function Root() {
  const [route, setRoute] = useState(currentRoute)
  const [user, setUser] = useState<UserInfo | null>(null)
  const [checking, setChecking] = useState(Boolean(readToken()))
  const [fontSize, setFontSize] = useState(readFontSize)

  useLayoutEffect(() => {
    const root = document.documentElement
    root.style.setProperty('--app-font-size', `${fontSize}px`)
    return () => { root.style.removeProperty('--app-font-size') }
  }, [fontSize])

  function changeFontSize(size: number) {
    setFontSize(size)
    saveFontSize(size)
  }

  useEffect(() => {
    const routeChanged = () => setRoute(currentRoute())
    const expired = () => { setUser(null); setChecking(false); window.location.hash = '#/login' }
    window.addEventListener('hashchange', routeChanged)
    window.addEventListener('tongxue-auth-expired', expired)
    if (readToken()) {
      getCurrentUser().then(setUser).catch(expired).finally(() => setChecking(false))
    } else if (currentRoute() !== '/register') {
      window.location.hash = '#/login'
    }
    return () => { window.removeEventListener('hashchange', routeChanged); window.removeEventListener('tongxue-auth-expired', expired) }
  }, [])

  function authenticated(token: string, account: UserInfo) {
    saveToken(token)
    setUser(account)
    window.location.hash = '#/'
    setRoute('/')
  }

  function logout() {
    clearToken()
    setUser(null)
    window.location.hash = '#/login'
  }

  if (checking) return <main className="auth-page">正在验证登录状态…</main>
  if (!user) return <AuthPage mode={route === '/register' ? 'register' : 'login'} onAuthenticated={authenticated} />
  const projectId = route.startsWith('/projects/') ? decodeURIComponent(route.slice('/projects/'.length)) : null
  const settingsSection = route === '/settings/models' ? 'models' : route === '/settings/appearance' ? 'appearance' : route === '/settings/output' ? 'output' : undefined
  const view = projectId || route === '/projects' ? 'projects'
    : route === '/users' && user.identity === 1 ? 'users'
    : route === '/mcp' && user.identity === 1 ? 'mcp'
    : route === '/roommate' ? 'roommate' : route === '/solve' ? 'solve'
    : route === '/settings' || settingsSection ? 'settings' : 'workspace'
  return <App user={user} onUserChange={setUser} onLogout={logout} view={view} projectId={projectId}
    settingsSection={settingsSection} fontSize={fontSize} onFontSizeChange={changeFontSize} />
}
