import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { searchUsers, updateUser } from '../api'
import UserList from './UserList'

vi.mock('../api', () => ({ assetUrl: (path: string) => path, searchUsers: vi.fn(), updateUser: vi.fn(), deleteUser: vi.fn() }))

afterEach(cleanup)

test('admin sees account timestamps and edits a normal user remark', async () => {
  const account = { id: 2, user_name: 'alice', identity: 0, status: 0, image: '',
    create_time: '2026-11-16T09:30:00', update_time: '2026-11-16T10:45:00', remark: '待复核' }
  let current = account
  vi.mocked(searchUsers).mockImplementation(async () => ({ items: [current], total: 1, page: 1, page_size: 10 }))
  vi.mocked(updateUser).mockImplementation(async () => { current = { ...account, remark: '已复核' }; return current })
  const user = userEvent.setup()
  render(<UserList currentUser={{ id: 1, user_name: 'admin', identity: 1, status: 0, image: '' }} />)

  expect(await screen.findByText('备注：待复核')).toBeInTheDocument()
  expect(screen.getByText(/创建：.*09:30/)).toBeInTheDocument()
  expect(screen.getByText(/修改：.*10:45/)).toBeInTheDocument()
  expect(screen.getByText(/创建：.*09:30/).parentElement).toBe(screen.getByText('备注：待复核').parentElement)
  await user.click(screen.getByRole('button', { name: '编辑' }))
  const remark = screen.getByRole('textbox', { name: '备注' })
  expect(remark).toHaveValue('待复核')
  await user.clear(remark)
  await user.type(remark, '已复核')
  await user.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(updateUser).toHaveBeenCalledWith(2, expect.objectContaining({ remark: '已复核' })))
  expect(await screen.findByText('备注：已复核')).toBeInTheDocument()
})

test('searches usernames and changes pages', async () => {
  vi.mocked(searchUsers).mockResolvedValue({ items: [], total: 21, page: 1, page_size: 10 })
  const user = userEvent.setup()
  render(<UserList currentUser={{ id: 1, user_name: 'admin', identity: 1, status: 0, image: '' }} />)
  await waitFor(() => expect(searchUsers).toHaveBeenCalledWith('', 1))
  await user.click(screen.getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(searchUsers).toHaveBeenCalledWith('', 2))
  await user.type(screen.getByRole('textbox', { name: '按用户名搜索' }), 'alice')
  await waitFor(() => expect(searchUsers).toHaveBeenCalledWith('alice', 1))
})
