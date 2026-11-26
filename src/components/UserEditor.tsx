import { componentText } from './constants'
import { useState, type FormEvent } from 'react'
import type { UserInfo, UserUpdate } from '../api'

type Props = {
  user: UserInfo
  isAdmin: boolean
  onSave: (update: UserUpdate, avatar: File | null) => Promise<void>
  onClose: () => void
}

export default function UserEditor({ user, isAdmin, onSave, onClose }: Props) {
  const [name, setName] = useState(user.user_name)
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState(user.status)
  const [identity, setIdentity] = useState(user.identity)
  const [remark, setRemark] = useState(user.remark ?? '')
  const [avatar, setAvatar] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    setError('')
    try {
      await onSave({ user_name: name.trim(), ...(password ? { user_password: password } : {}), ...(isAdmin ? { status, identity, ...(identity === 0 ? { remark } : {}) } : {}) }, avatar)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : componentText.userSaveFailed)
    } finally {
      setPending(false)
    }
  }

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <form className="user-dialog" role="dialog" aria-modal="true" aria-label="编辑用户信息" onSubmit={submit}>
      <h2>编辑用户信息</h2>
      <label>用户名<input value={name} onChange={(event) => setName(event.target.value)} maxLength={50} required /></label>
      <label>新密码（留空不修改）<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} autoComplete="new-password" /></label>
      {!isAdmin && <label>上传头像<input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setAvatar(event.target.files?.[0] ?? null)} /></label>}
      {avatar && <small>已选择：{avatar.name}</small>}
      {isAdmin && <label>账号状态<select value={status} onChange={(event) => setStatus(Number(event.target.value))}><option value={0}>正常</option><option value={1}>禁用</option></select></label>}
      {isAdmin && <label>账号身份<select value={identity} onChange={(event) => setIdentity(Number(event.target.value))}><option value={0}>普通用户</option><option value={1}>管理员</option></select></label>}
      {isAdmin && identity === 0 && <label>备注<textarea value={remark} onChange={(event) => setRemark(event.target.value)} maxLength={2000} rows={3} /></label>}
      {error && <p role="alert" className="auth-card__error">{error}</p>}
      <div className="user-dialog__actions"><button type="button" onClick={onClose}>取消</button><button type="submit" disabled={pending}>保存</button></div>
    </form>
  </div>
}
