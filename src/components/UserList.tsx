import { componentText } from './constants'
import { useEffect, useState } from 'react'
import { assetUrl, deleteUser, searchUsers, updateUser, type UserInfo, type UserUpdate } from '../api'
import UserEditor from './UserEditor'

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
}

export default function UserList({ currentUser }: { currentUser: UserInfo }) {
  const [users, setUsers] = useState<UserInfo[]>([])
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [revision, setRevision] = useState(0)
  const [editing, setEditing] = useState<UserInfo | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    const timer = window.setTimeout(() => {
      searchUsers(search, page).then((result) => {
        if (active) { setUsers(result.items); setTotal(result.total); setError('') }
      }).catch((cause) => { if (active) setError(cause.message) })
    }, search ? 250 : 0)
    return () => { active = false; window.clearTimeout(timer) }
  }, [search, page, revision])

  async function remove(user: UserInfo) {
    if (!window.confirm(`确定删除用户“${user.user_name}”吗？`)) return
    try {
      await deleteUser(user.id)
      if (users.length === 1 && page > 1) setPage(page - 1)
      else setRevision((value) => value + 1)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : componentText.userDeleteFailed)
    }
  }

  async function save(update: UserUpdate) {
    if (!editing) return
    const saved = await updateUser(editing.id, update)
    setUsers((items) => items.map((item) => item.id === saved.id ? saved : item))
    setRevision((value) => value + 1)
    setEditing(null)
  }

  return <section className="users-page" aria-label="用户列表">
    <div className="users-page__heading"><h1>用户列表</h1><p>管理已注册的账号</p></div>
    <label className="users-page__search">按用户名搜索<input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1) }} placeholder="输入用户名" /></label>
    {error && <p role="alert" className="auth-card__error">{error}</p>}
    <div className="users-page__list">{users.map((user) => <div className="users-page__row" key={user.id}>
      <span className="user-avatar">{user.image ? <img src={assetUrl(user.image)} alt="" /> : user.user_name.slice(0, 1).toUpperCase()}</span>
      <div><strong>{user.user_name} <small>{user.identity === 1 ? '管理员' : '普通用户'} · {user.status === 0 ? '正常' : '禁用'}</small></strong><small className="users-page__meta"><span>创建：{formatTime(user.create_time)}</span><span>修改：{formatTime(user.update_time)}</span>{user.identity === 0 && <span>备注：{user.remark || '—'}</span>}</small></div>
      <button type="button" onClick={() => setEditing(user)}>编辑</button>
      <button type="button" disabled={user.id === currentUser.id} onClick={() => remove(user)}>删除</button>
    </div>)}</div>
    <nav className="users-page__pagination" aria-label="用户分页"><button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} / {Math.max(1, Math.ceil(total / 10))} 页 · 共 {total} 人</span><button type="button" disabled={page * 10 >= total} onClick={() => setPage(page + 1)}>下一页</button></nav>
    {editing && <UserEditor user={editing} isAdmin={editing.id !== currentUser.id} onSave={save} onClose={() => setEditing(null)} />}
  </section>
}
