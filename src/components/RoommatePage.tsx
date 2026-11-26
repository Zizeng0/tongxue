import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { FileImage, FileText, FolderOpen, History, LoaderCircle, MessageSquare, Paperclip, Plus, Send, Trash2 } from 'lucide-react'
import { deleteRoommateChat, getRoommateChats, getRoommateImage, getTaskProgress, sendRoommateMessage } from '../api'
import type { Conversation } from '../contracts/responses/conversation'
import { submitOnEnter } from '../utils/composer'
import { formatElapsed } from '../utils/time'
import FileDropzone from './FileDropzone'
import SolutionReply from './SolutionReply'

const maxImages = 4
const imageTypes = '.png,.jpg,.jpeg,.webp,.gif,.bmp'
const documentTypes = '.docx,.pdf,.pptx,.txt,.md'
const imageExtension = /\.(?:png|jpe?g|webp|gif|bmp)$/i
const templateExtension = /\.pptx$/i
const textExtension = /\.(?:txt|md)$/i
const paperExtension = /\.(?:pdf|docx)$/i
const maxImageSize = 25 * 1024 * 1024
const draftChat = ''
const emptyDraft = { question: '', attachments: [] as File[], error: '' }

type Draft = typeof emptyDraft
type PendingTask = { id: string; chatId: string; startedAt: number; draft: Draft }

function mergeChats(current: Conversation[], updates: Conversation[]) {
  const chats = new Map(current.map((chat) => [chat.id, chat]))
  for (const chat of updates) {
    const messages = new Map(chats.get(chat.id)?.messages.map((message) => [message.id, message]))
    for (const message of chat.messages) {
      if (!message.reply && messages.get(message.id)?.reply) continue
      messages.set(message.id, message)
    }
    chats.set(chat.id, { ...chat, messages: [...messages.values()].sort((a, b) => a.created_at.localeCompare(b.created_at)) })
  }
  return [...chats.values()].sort((a, b) => b.created_at.localeCompare(a.created_at))
}

function PendingReply({ requestId, startedAt, onConversation }: {
  requestId?: string | null
  startedAt: number
  onConversation?: (id: string) => void
}) {
  const [stage, setStage] = useState('正在提交任务')
  const [now, setNow] = useState(Date.now)
  const onConversationRef = useRef(onConversation)
  onConversationRef.current = onConversation

  useEffect(() => {
    let active = true
    let timer: number
    const poll = async () => {
      if (!requestId) return
      const progress = await getTaskProgress(requestId).catch(() => null)
      if (!active) return
      if (progress) {
        setStage(progress.stage)
        if (progress.conversation_id) onConversationRef.current?.(progress.conversation_id)
      }
      if (!progress?.done) timer = window.setTimeout(poll, 900)
    }
    timer = window.setTimeout(poll, 350)
    const clock = window.setInterval(() => setNow(Date.now()), 1000)
    return () => { active = false; window.clearTimeout(timer); window.clearInterval(clock) }
  }, [requestId])

  return <div className="conversation__assistant-wrap">
    <span className="conversation__elapsed" aria-live="off">已处理 {formatElapsed(Math.max(0, now - startedAt) / 1000)}</span>
    <article className="conversation__item conversation__item--assistant" aria-label="助手回复">
      <p className="conversation__progress" role="status"><LoaderCircle size={14} className="spin" aria-hidden="true" />{stage}</p>
    </article>
  </div>
}

function RoommateImage({ chatId, path, name }: { chatId: string; path: string; name: string }) {
  const [url, setUrl] = useState('')
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    let objectUrl = ''
    getRoommateImage(chatId, path).then((blob) => {
      if (!active) return
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
    }).catch(() => { if (active) setFailed(true) })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [chatId, path])

  if (url) return <a className="roommate-image" href={url} target="_blank" rel="noreferrer"><img src={url} alt={name} loading="lazy" /></a>
  return <span className="roommate-file"><FileImage size={15} />{name}{failed ? ' · 图片加载失败' : ''}</span>
}

export default function RoommatePage() {
  const [chats, setChats] = useState<Conversation[]>([])
  const [chatId, setChatId] = useState(draftChat)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [pending, setPending] = useState<PendingTask[]>([])
  const [deleting, setDeleting] = useState<string[]>([])
  const [historyState, setHistoryState] = useState<'loading' | 'ready' | 'error'>('loading')
  const thread = useRef<HTMLDivElement>(null)
  const followScroll = useRef(true)
  const deletedIds = useRef(new Set<string>())
  const chat = chats.find((item) => item.id === chatId)
  const { question, attachments, error } = drafts[chatId] ?? emptyDraft
  const waitingForId = chatId.startsWith('pending-') && pending.some((task) => task.chatId === chatId)
  const visiblePending = pending.filter((task) => task.chatId === chatId)
  const activeOnServer = chats.some((item) => item.messages.some((message) =>
    message.status === 'processing' && !pending.some((task) => task.id === message.request_id)))

  useEffect(() => {
    let active = true
    let timer: number
    async function load() {
      try {
        const { items } = await getRoommateChats()
        if (!active) return
        setChats((current) => mergeChats(current, items.filter((item) => !deletedIds.current.has(item.id))))
        setHistoryState('ready')
        if (items.some((item) => item.messages.some((message) => message.status === 'processing'))) {
          timer = window.setTimeout(load, 1700)
        }
      } catch {
        if (!active) return
        setHistoryState('error')
        if (activeOnServer) timer = window.setTimeout(load, 1700)
      }
    }
    void load()
    return () => { active = false; window.clearTimeout(timer) }
  }, [activeOnServer])

  useLayoutEffect(() => {
    const panel = thread.current
    if (panel && followScroll.current) panel.scrollTop = panel.scrollHeight
  }, [chatId, chat?.messages, pending, question, attachments.length])

  function updateDraft(patch: Partial<Draft>) {
    setDrafts((current) => ({ ...current, [chatId]: { ...emptyDraft, ...current[chatId], ...patch } }))
  }

  function switchChat(id = draftChat) {
    followScroll.current = true
    setChatId(id)
  }

  function addFiles(files: File[]) {
    if (files.some((file) => !imageExtension.test(file.name) && !templateExtension.test(file.name) && !textExtension.test(file.name) && !paperExtension.test(file.name))) {
      updateDraft({ error: '支持图片、DOCX、PDF、PPTX、TXT 和 Markdown。' })
      return
    }
    if (files.some((file) => file.size > (textExtension.test(file.name) ? 256 * 1024 : maxImageSize))) {
      updateDraft({ error: '图片、DOCX、PDF 和 PPTX 每份最多 25 MB，文本文件每份最多 256 KB。' })
      return
    }
    const next = [...attachments, ...files]
    const imageCount = next.filter((file) => imageExtension.test(file.name)).length
    if (imageCount > maxImages || next.length - imageCount > 4) {
      updateDraft({ error: '每次最多添加 4 张图片和 4 份文件。' })
      return
    }
    if (next.filter((file) => templateExtension.test(file.name)).length > 1) {
      updateDraft({ error: '每次请选择一个 PPTX 模板。' })
      return
    }
    updateDraft({ attachments: next, error: '' })
  }

  function resolveChat(localId: string, serverId: string) {
    if (localId === serverId || deletedIds.current.has(serverId)) return
    setChats((current) => {
      const local = current.find((item) => item.id === localId)
      if (!local) return current
      const saved = current.find((item) => item.id === serverId)
      const others = current.filter((item) => item.id !== localId)
      return saved ? others : mergeChats(others, [{ ...local, id: serverId }])
    })
    setPending((current) => current.map((task) => task.chatId === localId ? { ...task, chatId: serverId } : task))
    setDrafts((current) => {
      if (!(localId in current)) return current
      const { [localId]: draft, ...others } = current
      return { ...others, [serverId]: others[serverId] ?? draft }
    })
    setChatId((current) => current === localId ? serverId : current)
  }

  async function removeChat(item: Conversation) {
    if (!window.confirm(`确定删除会话“${item.title}”吗？历史记录会归档，文件会保留。`)) return
    setDeleting((current) => [...current, item.id])
    try {
      await deleteRoommateChat(item.id)
      deletedIds.current.add(item.id)
      setChats((current) => current.filter((entry) => entry.id !== item.id))
      setDrafts((current) => {
        const { [item.id]: _draft, ...otherDrafts } = current
        return otherDrafts
      })
      setChatId((current) => current === item.id ? draftChat : current)
    } catch (cause) {
      setDrafts((current) => ({ ...current, [item.id]: { ...emptyDraft, ...current[item.id],
        error: cause instanceof Error ? cause.message : '删除会话失败，请重试。' } }))
    } finally {
      setDeleting((current) => current.filter((id) => id !== item.id))
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (waitingForId || deleting.includes(chatId) || (!question.trim() && !attachments.length)) return
    const id = crypto.randomUUID()
    const serverId = !chatId || chatId.startsWith('pending-') ? null : chatId
    const localId = chatId || `pending-${id}`
    const startedAt = Date.now()
    const draft = { question: question.trim(), attachments, error: '' }
    const task = { id, chatId: localId, startedAt, draft }
    followScroll.current = true
    if (!chat) setChats((current) => mergeChats(current, [{
      id: localId, title: draft.question.split('\n')[0].slice(0, 60) || '根据附件处理任务',
      created_at: new Date(startedAt).toISOString(), messages: [],
    }]))
    setChatId(localId)
    setDrafts((current) => ({ ...current, [chatId]: emptyDraft, [localId]: emptyDraft }))
    setPending((current) => [...current, task])
    let resolvedId = localId
    try {
      const images = attachments.filter((file) => imageExtension.test(file.name))
      const documents = attachments.filter((file) => !imageExtension.test(file.name))
      const result = await sendRoommateMessage(draft.question, images, serverId, id, documents)
      if (!deletedIds.current.has(result.id)) {
        resolveChat(localId, result.id)
        setChats((current) => mergeChats(current, [result]))
      }
    } catch (cause) {
      const progress = await getTaskProgress(id).catch(() => null)
      if (progress?.conversation_id) {
        resolvedId = progress.conversation_id
        resolveChat(localId, resolvedId)
        // The server accepted the task. Reconcile it even if the submit connection was interrupted.
        const saved = await getRoommateChats().catch(() => null)
        if (saved) setChats((current) => mergeChats(current, saved.items.filter((item) => !deletedIds.current.has(item.id))))
      }
      if (!progress?.conversation_id && !deletedIds.current.has(resolvedId)) {
        setDrafts((current) => {
          const existing = current[resolvedId]
          const restored = existing?.question || existing?.attachments.length ? existing : draft
          return { ...current, [resolvedId]: { ...restored, error: cause instanceof Error ? cause.message : '发送失败，请重试。' } }
        })
      }
    } finally {
      setPending((current) => current.filter((entry) => entry.id !== id))
    }
  }

  return <div className="content-layout roommate-layout">
    <section className="roommate" aria-label="室友对话">
      <div className="roommate__thread" role="log" aria-label="室友消息" ref={thread} onScroll={(event) => {
        const panel = event.currentTarget
        followScroll.current = panel.scrollHeight - panel.scrollTop - panel.clientHeight <= 48
      }}>
        {!chat?.messages.length && !visiblePending.length && <div className="roommate__welcome">
          <span><MessageSquare size={25} aria-hidden="true" /></span>
          <h1>室友</h1>
          <p>描述你要完成的任务，也可以附上文件或图片。</p>
        </div>}
        {chat?.messages.filter((message) => !visiblePending.some((task) => task.id === message.request_id)).map((message) => <article className="roommate__turn" key={message.id}>
          <div className="roommate__user">
            <p>{message.requirements}</p>
            {message.attachment_names.length > 0 && <div className="roommate__images">{message.attachment_names.map((name, index) =>
              <RoommateImage key={index} chatId={chat.id} path={`input-images/${message.id}/${index}`} name={name} />)}</div>}
            {!!message.other_file_names?.length && <div className="roommate__attachments">{message.other_file_names.map((name, index) =>
              <span className="roommate-file" key={index}><FileText size={15} />{name}</span>)}</div>}
          </div>
          {message.status === 'processing' && <PendingReply requestId={message.request_id} startedAt={Date.parse(message.created_at)} />}
          {message.reply && <div className="conversation__assistant-wrap">
            {message.processing_seconds != null && <span className="conversation__elapsed">已处理 {formatElapsed(message.processing_seconds)}</span>}
            <article className={`conversation__item conversation__item--assistant ${message.status === 'error' ? 'conversation__item--error' : ''}`} aria-label="助手回复">
              <SolutionReply text={message.reply} renderImage={(path) => message.output_images?.includes(path)
                ? <RoommateImage chatId={chat.id} path={path.replace(/^solutions\//, 'images/')} name="题解图片" /> : null} />
              {message.output_images?.filter((path) => !message.reply?.includes(`](${path})`)).map((path) =>
                <RoommateImage key={path} chatId={chat.id} path={path.replace(/^solutions\//, 'images/')} name="题解图片" />)}
              {!!(message.artifact_files?.length || message.report_files?.length) &&
                <a className="roommate__project" href={`#/projects/${encodeURIComponent(chat.id)}`}><FolderOpen size={16} />在我的项目查看</a>}
            </article>
          </div>}
        </article>)}
        {visiblePending.map((task) => <article className="roommate__turn" key={task.id}>
          <div className="roommate__user"><p>{task.draft.question || '根据附件处理任务'}</p>
            {!!task.draft.attachments.length && <div className="roommate__attachments">{task.draft.attachments.map((file, index) =>
              <span className="roommate-file" key={index}><Paperclip size={15} />{file.name}</span>)}</div>}
          </div>
          <PendingReply requestId={task.id} startedAt={task.startedAt} onConversation={(id) => resolveChat(task.chatId, id)} />
        </article>)}
      </div>
      <form className="roommate__composer" onSubmit={submit}>
        {error && <p className="auth-card__error" role="alert">{error}</p>}
        <label className="visually-hidden" htmlFor="roommate-prompt">给室友发消息</label>
        <div className="roommate__input" data-value={question}>
          <textarea id="roommate-prompt" value={question} maxLength={16000} rows={1}
            onChange={(event) => updateDraft({ question: event.target.value })} onKeyDown={(event) => submitOnEnter(event, waitingForId || deleting.includes(chatId))}
            placeholder="输入任务，例如制作课程汇报或解释一道题" />
        </div>
        <div className="roommate__actions">
          <div className="roommate__upload" role="group" aria-label="任务附件">
            <FileDropzone accept={`${imageTypes},${documentTypes}`} multiple onFiles={addFiles} icon={<Plus size={20} />}
              files={attachments} onRemoveFile={(index) => updateDraft({ attachments: attachments.filter((_, position) => position !== index), error: '' })}
              title="添加附件" hint="图片、DOCX、PDF、PPTX、TXT、Markdown" />
          </div>
          <button className="roommate__send" type="submit" title="Enter 发送，Shift+Enter 换行"
            disabled={waitingForId || deleting.includes(chatId) || (!question.trim() && !attachments.length)}><Send size={16} />发送</button>
        </div>
      </form>
    </section>
    <aside className="history-sidebar roommate-history" aria-label="室友历史对话">
      <div className="history-sidebar__heading"><h2><History size={17} />历史对话</h2></div>
      <button className="history-sidebar__new" type="button" onClick={() => {
        setDrafts((current) => ({ ...current, [draftChat]: emptyDraft }))
        switchChat()
      }}><Plus size={17} />新对话</button>
      {chats.length > 0 ? <nav className="roommate-history__list" aria-label="已保存对话">{chats.map((item) => {
        const processing = pending.some((task) => task.chatId === item.id) || item.messages.some((message) => message.status === 'processing')
        return <div className="roommate-history__row" key={item.id}>
          <button className="roommate-history__select" type="button" aria-pressed={item.id === chatId} onClick={() => switchChat(item.id)} title={item.title}>{item.title}</button>
          {processing && <span className="roommate-history__processing" role="img" aria-label="处理中" title="处理中"><LoaderCircle size={14} className="spin" /></span>}
          <button className="roommate-history__delete" type="button" disabled={item.id.startsWith('pending-') || deleting.includes(item.id)}
            aria-label={`删除会话 ${item.title}`} title="删除会话" onClick={() => void removeChat(item)}><Trash2 size={15} /></button>
        </div>
      })}</nav>
        : <p className="history-sidebar__empty">{historyState === 'loading' ? '正在加载历史对话…' : historyState === 'error' ? '历史对话暂不可用' : '暂无历史对话'}</p>}
    </aside>
  </div>
}
