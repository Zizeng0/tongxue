import { componentText } from './constants'
import { useState, type FormEvent } from 'react'
import { LockKeyhole, Sparkles } from 'lucide-react'
import { login, register, type UserInfo } from '../api'

type Props = { mode: 'login' | 'register'; onAuthenticated: (token: string, user: UserInfo) => void }

export default function AuthPage({ mode, onAuthenticated }: Props) {
  const [userName, setUserName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (mode === 'register' && password !== confirmation) return setError(componentText.passwordMismatch)
    setPending(true)
    setError('')
    try {
      const response = mode === 'login'
        ? await login(userName.trim(), password)
        : await register(userName.trim(), password, confirmation)
      onAuthenticated(response.access_token, response.user)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : componentText.authFailed)
    } finally {
      setPending(false)
    }
  }

  return <main className="auth-page">
    <form className="auth-card" onSubmit={submit}>
      <div className="auth-card__mark"><Sparkles size={23} /></div>
      <h1>{mode === 'login' ? '登录同学工作台' : '注册账号'}</h1>
      <p>继续管理你的实验任务</p>
      <label>用户名<input autoComplete="username" value={userName} onChange={(event) => setUserName(event.target.value)} maxLength={50} required /></label>
      <label>用户密码<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} required /></label>
      {mode === 'register' && <label>确认密码<input type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} minLength={8} required /></label>}
      {error && <p className="auth-card__error" role="alert">{error}</p>}
      <button className="auth-card__submit" disabled={pending} type="submit"><LockKeyhole size={16} />{pending ? '请稍候…' : mode === 'login' ? '登录' : '注册'}</button>
      <button className="auth-card__switch" type="button" onClick={() => { setError(''); window.location.hash = mode === 'login' ? '#/register' : '#/login' }}>
        {mode === 'login' ? '没有账号？立即注册' : '已有账号？返回登录'}
      </button>
    </form>
  </main>
}
