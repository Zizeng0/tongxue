import { componentText } from './constants'
import { workspaceText } from '../constants'
import { Fragment, memo, useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, FileImage, History, LoaderCircle, Paperclip, Plus, Send, Sparkles, Trash2 } from 'lucide-react'
import { createSolution, deleteSolution, getSolutionImage, getSolutionInputImage, getSolutions } from '../api'
import type { Conversation } from '../types'
import { formatElapsed } from '../utils/time'
import { submitOnEnter } from '../utils/composer'
import FileDropzone from './FileDropzone'
import SolutionReply from './SolutionReply'

const imageExtensions = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']
const maxImageSize = 25 * 1024 * 1024

function SolutionInputImage({ conversationId, messageId, file }: { conversationId: string; messageId: string; file?: File }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    let current = true
    let objectUrl = ''
    if (file) {
      objectUrl = URL.createObjectURL(file)
      setUrl(objectUrl)
    } else {
      getSolutionInputImage(conversationId, messageId).then((blob) => {
        if (!current) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      }).catch(() => {})
    }
    return () => { current = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [conversationId, messageId, file])
  return url ? <img className="solution-input-image" src={url} alt="上传的题目图片" /> : null
}

function SolutionImage({ conversationId, imagePath }: { conversationId: string; imagePath: string }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    let current = true
    let objectUrl = ''
    getSolutionImage(conversationId, imagePath).then((blob) => {
      if (!current) return
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
    }).catch(() => {})
    return () => { current = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [conversationId, imagePath])
  return url ? <img className="solution-image" src={url} alt="题解生成图" /> : null
}

const SolutionOutput = memo(function SolutionOutput({ text, conversationId, images }: { text: string; conversationId: string; images: string[] }) {
  const referenced = new Set(Array.from(text.matchAll(/!\[[^\]\n]*\]\((solutions\/[^)\s]+)\)/g), (match) => match[1]))
  return <>
    <SolutionReply text={text} renderImage={(path) => images.includes(path) ? <SolutionImage conversationId={conversationId} imagePath={path} /> : null} />
    {images.filter((path) => !referenced.has(path)).map((path) => <SolutionImage key={path} conversationId={conversationId} imagePath={path} />)}
  </>
}, (first, second) => first.text === second.text && first.conversationId === second.conversationId &&
  first.images.length === second.images.length && first.images.every((path, index) => path === second.images[index]))

export default function SolutionPage() {
  const conversationRef = useRef<HTMLElement>(null)
  const latestAnswerRef = useRef<HTMLDivElement>(null)
  const activeIdRef = useRef<string | null>(null)
  const userSelected = useRef(false)
  const deletedIds = useRef(new Set<string>())
  const pendingRequest = useRef<{ id: string; conversationId: string } | null>(null)
  const followScroll = useRef(true)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [image, setImage] = useState<File | null>(null)
  const [question, setQuestion] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [clockTime, setClockTime] = useState(Date.now)
  const progressStartedAt = useRef(0)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [historyError, setHistoryError] = useState(false)
  const [error, setError] = useState('')
  const activeConversation = conversations.find((item) => item.id === activeId)
  const messages = activeConversation?.messages ?? []
  const hasProcessingMessage = messages.some((message) => message.status === 'processing')

  useEffect(() => {
    let mounted = true
    let timer: number
    const refresh = async () => {
      try {
        const { items } = await getSolutions()
        if (!mounted) return
        const loaded = items.filter((item) => !deletedIds.current.has(item.id))
        const pending = pendingRequest.current
        setConversations((current) => {
          const byId = new Map(current.map((item) => [item.id, item]))
          for (const item of loaded) {
            const progress = pending && item.messages.find((message) => message.request_id === pending.id)
            if (pending && (item.id === pending.conversationId || progress)) {
              const local = byId.get(pending.conversationId)
              if (local && progress) {
                const messages = local.messages.map((message) => {
                  if (!message.pending) return message
                  const candidates = progress.exercise_candidates ?? message.exercise_candidates
                  const status = progress.vision_status ?? message.vision_status
                  if (candidates === message.exercise_candidates && status === message.vision_status) return message
                  return { ...message, exercise_candidates: candidates, vision_status: status }
                })
                if (messages.some((message, index) => message !== local.messages[index])) {
                  byId.set(local.id, { ...local, messages })
                }
              }
              continue
            }
            const existing = byId.get(item.id)
            if (existing?.messages.some((message) => message.pending)) continue
            const savedMessages = new Map(existing?.messages.map((message) => [message.id, message]))
            for (const message of item.messages) {
              const saved = savedMessages.get(message.id)
              if (saved?.reply && !message.reply) continue
              savedMessages.set(message.id, message)
            }
            const merged = { ...item, messages: [...savedMessages.values()] }
            if (existing && JSON.stringify(existing) === JSON.stringify(merged)) continue
            byId.set(item.id, merged)
          }
          if (current.length === byId.size && current.every((item) => byId.get(item.id) === item)) return current
          return [...byId.values()].sort((first, second) => first.created_at.localeCompare(second.created_at))
        })
        if (!userSelected.current) {
          activeIdRef.current = loaded.at(-1)?.id ?? null
          setActiveId(activeIdRef.current)
        }
        setHistoryError(false)
      } catch { if (mounted) setHistoryError(true) }
      finally { if (mounted) timer = window.setTimeout(refresh, 3000) }
    }
    void refresh()
    return () => { mounted = false; window.clearTimeout(timer) }
  }, [])

  useEffect(() => {
    const panel = conversationRef.current
    if (!followScroll.current || !panel) return
    const answer = latestAnswerRef.current
    panel.scrollTop = answer
      ? panel.scrollTop + answer.getBoundingClientRect().top - panel.getBoundingClientRect().top
      : panel.scrollHeight
  }, [activeId, messages.length, messages.at(-1)?.reply, messages.at(-1)?.exercise_candidates])

  useEffect(() => {
    if (!submitting && !hasProcessingMessage) return
    const timer = window.setInterval(() => setClockTime(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [submitting, hasProcessingMessage])

  function chooseConversation(conversationId: string | null) {
    followScroll.current = true
    userSelected.current = true
    activeIdRef.current = conversationId
    setActiveId(conversationId)
    setImage(null)
    setQuestion('')
    setError('')
  }

  function addImage(files: File[]) {
    const file = files[0]
    if (!imageExtensions.some((extension) => file.name.toLowerCase().endsWith(extension))) {
      setError(componentText.solutionImageType)
      return
    }
    if (file.size > maxImageSize) {
      setError(componentText.solutionImageSize)
      return
    }
    setImage(file)
    setError('')
  }

  async function removeConversation(conversation: Conversation) {
    if (!window.confirm(`确定归档会话“${conversation.title}”吗？记录和文件会保留。`)) return
    setDeletingId(conversation.id)
    setError('')
    try {
      await deleteSolution(conversation.id)
      deletedIds.current.add(conversation.id)
      const remaining = conversations.filter((item) => item.id !== conversation.id)
      setConversations(remaining)
      if (activeId === conversation.id) chooseConversation(remaining.at(-1)?.id ?? null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : workspaceText.archiveFailed)
    } finally {
      setDeletingId(null)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pendingRequest.current) return
    if (!question.trim() && !image) {
      setError(componentText.questionRequired)
      return
    }
    const submittedQuestion = question.trim()
    const submittedImage = image
    const submittedId = activeId
    const requestId = crypto.randomUUID()
    const optimisticId = submittedId ?? `pending-${requestId}`
    const messageId = `pending-${requestId}`
    const now = new Date().toISOString()
    const text = submittedQuestion || '请解答图片中的题目'
    const optimisticMessage = { id: messageId, created_at: now, requirements: text, guide_name: null,
      attachment_names: submittedImage ? [submittedImage.name] : [], preview_file: submittedImage ?? undefined, pending: true }
    if (submittedId) {
      setConversations((current) => current.map((item) => item.id === submittedId
        ? { ...item, messages: [...item.messages, optimisticMessage] } : item))
    } else {
      setConversations((current) => [...current, { id: optimisticId, title: text.slice(0, 60),
        created_at: now, messages: [optimisticMessage] }])
      activeIdRef.current = optimisticId
      setActiveId(optimisticId)
    }
    userSelected.current = true
    setQuestion('')
    setImage(null)
    setError('')
    pendingRequest.current = { id: requestId, conversationId: optimisticId }
    followScroll.current = true
    progressStartedAt.current = Date.now()
    setClockTime(progressStartedAt.current)
    setSubmitting(true)
    try {
      const saved = await createSolution(submittedQuestion, submittedImage, submittedId, requestId)
      setConversations((current) => [...current.filter((item) => item.id !== optimisticId && item.id !== saved.id), saved]
        .sort((first, second) => first.created_at.localeCompare(second.created_at)))
      if (activeIdRef.current === optimisticId) activeIdRef.current = saved.id
      setActiveId((current) => current === optimisticId ? saved.id : current)
    } catch (cause) {
      setConversations((current) => submittedId
        ? current.map((item) => item.id === submittedId
          ? { ...item, messages: item.messages.filter((message) => message.id !== messageId) } : item)
        : current.filter((item) => item.id !== optimisticId))
      if (activeIdRef.current === optimisticId) {
        if (!submittedId) { activeIdRef.current = null; setActiveId(null) }
        setQuestion((current) => current || submittedQuestion)
        setImage((current) => current ?? submittedImage)
        setError(cause instanceof Error ? cause.message : componentText.solutionFailed)
      }
    } finally {
      pendingRequest.current = null
      setSubmitting(false)
    }
  }

  return <div className="content-layout solution-layout">
    <div className="page-wrap">
      <section className="conversation" aria-label="解题对话" ref={conversationRef} onScroll={(event) => {
        const panel = event.currentTarget
        followScroll.current = panel.scrollHeight - panel.scrollTop - panel.clientHeight <= 48
      }}>
        {messages.map((message, index) => {
          const processing = message.pending || message.status === 'processing'
          const seconds = processing
            ? Math.max(0, Math.floor((clockTime - (message.pending ? progressStartedAt.current : Date.parse(message.created_at))) / 1000))
            : message.processing_seconds
          return <Fragment key={message.id}>
          <article className="conversation__item conversation__item--user" aria-label="用户消息">
            <p className="conversation__text">{message.requirements}</p>
            {message.attachment_names.map((name) => <div className="conversation__files" key={name}><span className="conversation__file" title={name}><FileImage size={14} /><span>题目图片 · {name}</span></span></div>)}
            {message.attachment_names.length > 0 && activeConversation && <SolutionInputImage conversationId={activeConversation.id} messageId={message.id} file={message.preview_file} />}
            <span className="conversation__status">{message.pending || message.status === 'processing' ? <LoaderCircle size={14} className="spin" /> : <CheckCircle2 size={14} />}{message.pending || message.status === 'processing' ? '正在解题' : message.status === 'error' ? '处理失败' : '已回复'}</span>
          </article>
          {(message.reply || processing) && <div className="conversation__assistant-wrap"
            ref={(message.reply || message.exercise_candidates) && index === messages.length - 1 ? latestAnswerRef : undefined}>
            {seconds != null && <span className="conversation__elapsed">已处理 {formatElapsed(seconds)}</span>}
            <article className={`conversation__item conversation__item--assistant ${message.status === 'error' ? 'conversation__item--error' : ''}`} aria-label="助手回复">
              {message.reply && activeConversation
                ? <SolutionOutput text={message.reply} conversationId={activeConversation.id} images={message.output_images ?? []} />
                : <p className="conversation__progress" role="status"><LoaderCircle size={14} className="spin" />
                  {message.exercise_candidates ? '候选待核对，正在继续处理题图…' : '正在识别题目并检索教材…'}
                </p>}
              {message.vision_status && <p className="conversation__status">{message.vision_status}</p>}
              {message.exercise_candidates && !message.reply?.includes(message.exercise_candidates) &&
                <section aria-label="相似题目"><SolutionReply text={message.exercise_candidates} /></section>}
            </article>
          </div>}
          </Fragment>
        })}
      </section>
      <form className="workspace-grid" onSubmit={handleSubmit}>
        <section className="panel task-panel" aria-labelledby="solution-image-title">
          <div className="panel__heading"><div className="section-icon section-icon--violet"><Paperclip size={19} /></div><h2 id="solution-image-title">题目图片</h2><span className="optional">选填</span></div>
          <FileDropzone accept="image/png,image/jpeg,image/webp,image/gif,image/bmp" onFiles={addImage} file={image} onRemove={() => setImage(null)} icon={<Plus size={20} />} title="上传题目图片" hint="最大 25 MB" />
        </section>
        <section className="panel requirements-panel" aria-labelledby="solution-input-title">
          <div className="panel__heading"><div className="section-icon section-icon--orange"><Sparkles size={19} /></div><h2 id="solution-input-title">题目描述</h2><span className="optional">选填</span></div>
          <div className="composer"><textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => submitOnEnter(event, submitting)} aria-label="题目描述" placeholder="输入题目或补充图片中的条件…" rows={3} />
            <button className="primary-button" type="submit" disabled={submitting}>{submitting ? <LoaderCircle size={17} className="spin" /> : <Send size={17} />}发送</button>
          </div>
        </section>
        {error && <div className="message message--error" role="alert">{error}</div>}
      </form>
    </div>
    <aside className="history-sidebar" aria-labelledby="solution-history-title">
      <div className="history-sidebar__heading"><h2 id="solution-history-title"><History size={17} />历史对话信息</h2><span>{conversations.length}</span></div>
      <button className="history-sidebar__new" type="button" onClick={() => chooseConversation(null)}><Plus size={17} />新增会话</button>
      {conversations.length ? <nav className="history-list" aria-label="解题历史">{[...conversations].reverse().map((conversation) => <div className="history-list__row" key={conversation.id}>
        <button type="button" className={`history-list__item ${conversation.id === activeId ? 'history-list__item--active' : ''}`} aria-pressed={conversation.id === activeId} onClick={() => chooseConversation(conversation.id)}><span className="history-list__icon"><History size={15} /></span><span className="history-list__title">{conversation.title}</span></button>
        <button className="history-list__delete" type="button" aria-label={`归档会话 ${conversation.title}`} disabled={deletingId === conversation.id || conversation.id.startsWith('pending-')} onClick={() => removeConversation(conversation)}><Trash2 size={15} /></button>
        <span className="history-list__date" role="tooltip">创建于 {new Date(conversation.created_at).toLocaleString('zh-CN')}</span>
      </div>)}</nav> : <p className="history-sidebar__empty">{historyError ? '历史记录暂不可用' : '暂无历史对话'}</p>}
    </aside>
  </div>
}
