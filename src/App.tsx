import { workspaceText } from './constants'
import { Fragment, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  Check, CheckCircle2, ClipboardList, CloudUpload, FileCode2,
  FileImage, FileText, FolderOpen, History, LayoutDashboard, LoaderCircle,
  MessageSquare, Paperclip, Plug, Plus, Send, Settings2, Sparkles, Trash2, Users, X,
} from 'lucide-react'
import { assetUrl, createTask, deleteTask, downloadReport, getSoftware, getTaskProgress, getTasks, selectSoftware, updateMe, uploadAvatar, type UserInfo, type UserUpdate } from './api'
import FileDropzone from './components/FileDropzone'
import SoftwarePicker from './components/SoftwarePicker'
import UserEditor from './components/UserEditor'
import UserList from './components/UserList'
import ProjectPage from './components/ProjectPage'
import SettingsPage from './components/SettingsPage'
import { settingsText } from './components/constants'
import SolutionPage from './components/SolutionPage'
import RoommatePage from './components/RoommatePage'
import McpPage from './components/McpPage'
import type { Conversation, Deliverable, Software } from './types'
import { formatElapsed } from './utils/time'
export { formatElapsed } from './utils/time'

const defaultSoftware: Software[] = [
  { id: 'modelsim', name: 'Modelsim SE-64 10.1c' },
  { id: 'quartus', name: 'Quartus II 13.1 (64-bit)' },
  { id: 'packet-tracer', name: 'Cisco Packet Tracer' },
  { id: 'hfss', name: 'Ansys HFSS' },
  { id: 'pycharm', name: 'PyCharm' },
]
const deliverableOptions = [
  { id: 'report', label: '实验报告', icon: FileText },
  { id: 'paper', label: '论文', icon: ClipboardList },
  { id: 'code', label: '源文件', icon: FileCode2 },
] as const
const attachmentExtensions = ['.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']
const otherFileExtensions = [...attachmentExtensions, '.txt', '.csv', '.tsv', '.json', '.py', '.xlsx', '.xls', '.npy', '.doc', '.docx']
const maxPdfSize = 100 * 1024 * 1024
const maxGuideSize = 120 * 1024 * 1024
const maxImageSize = 25 * 1024 * 1024
const maxReportTemplateSize = 10 * 1024 * 1024

function formatSize(size: number) {
  return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function mergeConversations(current: Conversation[], loaded: Conversation[]): Conversation[] {
  const byId = new Map(current.map((item) => [item.id, item]))
  for (const item of loaded) {
    const existing = byId.get(item.id)
    // Keep a local upload visible until its POST returns the real message ID.
    if (!existing?.messages.some((message) => message.pending)) byId.set(item.id, item)
  }
  return [...byId.values()].sort((first, second) => first.created_at.localeCompare(second.created_at))
}

type AppProps = { user?: UserInfo; onUserChange?: (user: UserInfo) => void; onLogout?: () => void; view?: 'workspace' | 'users' | 'projects' | 'solve' | 'roommate' | 'settings' | 'mcp'; projectId?: string | null; settingsSection?: 'models' | 'appearance' | 'output'; fontSize?: number; onFontSizeChange?: (size: number) => void }

export default function App({ user, onUserChange, onLogout, view: requestedView = 'workspace', projectId = null, settingsSection, fontSize, onFontSizeChange }: AppProps) {
  const isAdmin = user?.identity === 1
  const view = requestedView === 'mcp' && !isAdmin ? 'workspace' : requestedView
  const conversationRef = useRef<HTMLElement>(null)
  const profileMenuRef = useRef<HTMLDivElement>(null)
  const activeConversationIdRef = useRef<string | null>(null)
  // 用户主动切换后，不让较晚返回的历史请求覆盖当前选择。
  const hasChosenConversation = useRef(false)
  const deletedConversationIds = useRef(new Set<string>())
  const [guide, setGuide] = useState<File | null>(null)
  const [reportTemplate, setReportTemplate] = useState<File | null>(null)
  const [attachments, setAttachments] = useState<File[]>([])
  const [otherFiles, setOtherFiles] = useState<File[]>([])
  const [requirements, setRequirements] = useState('')
  const [deliverables, setDeliverables] = useState<Deliverable[]>(['report'])
  const [software, setSoftware] = useState<Software[]>(defaultSoftware)
  const [selectedSoftware, setSelectedSoftware] = useState('hfss')
  const [softwarePending, setSoftwarePending] = useState<string | null>(null)
  const [serviceState, setServiceState] = useState<'loading' | 'ready' | 'offline'>('loading')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [progressStage, setProgressStage] = useState('')
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [historyError, setHistoryError] = useState(false)
  const [deletingConversationId, setDeletingConversationId] = useState<string | null>(null)
  const [profileMenuOpen, setProfileMenuOpen] = useState(false)
  const [editingProfile, setEditingProfile] = useState(false)
  const activeConversation = conversations.find((item) => item.id === activeConversationId)
  const activeSoftwareId = activeConversation?.messages.at(-1)?.software_id
  const messages = activeConversation?.messages ?? []
  const activePendingMessage = [...messages].reverse().find((message) => message.pending || message.status === 'processing')
  const progressRequestId = activePendingMessage?.request_id ?? null
  const activeConversationBusy = Boolean(activePendingMessage)
  const hasRunningTasks = conversations.some((conversation) => conversation.messages.some((message) => message.status === 'processing'))
  const savedGuideName = messages.find((message) => message.guide_name)?.guide_name

  useEffect(() => {
    // 软件列表以本地服务为准；断线时保留预设选项供界面展示。
    let active = true
    getSoftware().then((response) => {
      if (!active) return
      setSoftware(response.items)
      setSelectedSoftware(response.selected_id ?? 'hfss')
      setServiceState('ready')
    }).catch(() => {
      if (active) setServiceState('offline')
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (activeSoftwareId && software.some((item) => item.id === activeSoftwareId)) {
      setSelectedSoftware(activeSoftwareId)
    }
  }, [activeConversationId, activeSoftwareId, software])

  useEffect(() => {
    // 合并已在当前页面发送的消息，避免首次加载与发送请求并发时重复显示。
    let active = true
    getTasks().then((response) => {
      if (!active) return
      const loaded = response.items.filter((item) => !deletedConversationIds.current.has(item.id))
      setConversations((current) => mergeConversations(current, loaded))
      if (!hasChosenConversation.current) {
        activeConversationIdRef.current = loaded.at(-1)?.id ?? null
        setActiveConversationId(activeConversationIdRef.current)
      }
    }).catch(() => { if (active) setHistoryError(true) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!hasRunningTasks) return
    let active = true
    let timer: number | undefined
    const refresh = async () => {
      try {
        const response = await getTasks()
        if (active) setConversations((current) => mergeConversations(current, response.items.filter((item) => !deletedConversationIds.current.has(item.id))))
      } catch { /* Keep the currently visible task while the service is unavailable. */ }
      if (active) timer = window.setTimeout(refresh, 3000)
    }
    timer = window.setTimeout(refresh, 3000)
    return () => { active = false; window.clearTimeout(timer) }
  }, [hasRunningTasks])

  useEffect(() => {
    if (conversationRef.current) conversationRef.current.scrollTop = conversationRef.current.scrollHeight
  }, [activeConversationId, messages.length, messages.at(-1)?.reply])

  useEffect(() => {
    if (!profileMenuOpen) return
    function closeOnOutside(event: PointerEvent) {
      if (!profileMenuRef.current?.contains(event.target as Node)) setProfileMenuOpen(false)
    }
    document.addEventListener('pointerdown', closeOnOutside)
    return () => document.removeEventListener('pointerdown', closeOnOutside)
  }, [profileMenuOpen])

  useEffect(() => {
    if (!progressRequestId) return
    let active = true
    let timer: number | undefined
    const refresh = async () => {
      try {
        const result = await getTaskProgress(progressRequestId)
        if (active) setProgressStage(result.stage)
        if (active && !result.done) timer = window.setTimeout(refresh, 3000)
      } catch {
        if (active) timer = window.setTimeout(refresh, 3000)
      }
    }
    const startedAt = new Date(activePendingMessage?.created_at ?? Date.now()).getTime()
    setProgressStage('正在处理')
    setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)))
    const clock = window.setInterval(() => setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000))), 1000)
    refresh()
    return () => { active = false; window.clearTimeout(timer); window.clearInterval(clock) }
  }, [progressRequestId])

  function chooseConversation(conversationId: string | null) {
    // 切换会话时清空未发送的输入，避免把文件或要求发到另一会话。
    hasChosenConversation.current = true
    activeConversationIdRef.current = conversationId
    setActiveConversationId(conversationId)
    const softwareId = conversations.find((item) => item.id === conversationId)?.messages.at(-1)?.software_id
    if (softwareId && software.some((item) => item.id === softwareId)) setSelectedSoftware(softwareId)
    setGuide(null)
    setReportTemplate(null)
    setAttachments([])
    setOtherFiles([])
    setRequirements('')
    setError('')
  }

  async function removeConversation(conversationId: string, title: string) {
    if (!window.confirm(`确定归档会话“${title}”吗？记录和文件会保留。`)) return
    setDeletingConversationId(conversationId)
    setError('')
    try {
      await deleteTask(conversationId)
      deletedConversationIds.current.add(conversationId)
      const remaining = conversations.filter((item) => item.id !== conversationId)
      setConversations(remaining)
      if (activeConversationId === conversationId) chooseConversation(remaining.at(-1)?.id ?? null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : workspaceText.archiveFailed)
    } finally {
      setDeletingConversationId(null)
    }
  }

  function addGuide(files: File[]) {
    if (activeConversationId) return setError(workspaceText.guideAlreadySet)
    const file = files[0]
    if (!file.name.toLowerCase().endsWith('.pdf')) return setError(workspaceText.guideType)
    if (file.size > maxGuideSize) return setError(workspaceText.guideSize)
    setGuide(file)
    setError('')
  }

  function addAttachments(files: File[]) {
    if (files.some((file) => !attachmentExtensions.some((extension) => file.name.toLowerCase().endsWith(extension)))) {
      return setError(workspaceText.attachmentType)
    }
    if (files.some((file) => file.size > (file.name.toLowerCase().endsWith('.pdf') ? maxPdfSize : maxImageSize))) {
      return setError(workspaceText.attachmentSize)
    }
    setAttachments((current) => [...current, ...files])
    setError('')
  }

  function addReportTemplate(files: File[]) {
    const file = files[0]
    if (!/\.docx?$/i.test(file.name)) return setError(workspaceText.templateType)
    if (file.size > maxReportTemplateSize) return setError(workspaceText.templateSize)
    setReportTemplate(file)
    setError('')
  }

  function addOtherFiles(files: File[]) {
    if (files.some((file) => !otherFileExtensions.some((extension) => file.name.toLowerCase().endsWith(extension)))) {
      return setError(workspaceText.fileType)
    }
    if (files.some((file) => file.size > (attachmentExtensions.slice(1).some((extension) => file.name.toLowerCase().endsWith(extension)) ? maxImageSize : maxPdfSize))) {
      return setError(workspaceText.fileSize)
    }
    setOtherFiles((current) => [...current, ...files])
    setError('')
  }

  function toggleDeliverable(id: Deliverable) {
    if (id === 'report' && deliverables.includes(id)) setReportTemplate(null)
    setDeliverables((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  }

  async function handleSoftwareSelect(softwareId: string) {
    setSelectedSoftware(softwareId)
    setError('')
    if (serviceState !== 'ready') return
    setSoftwarePending(softwareId)
    try {
      await selectSoftware(softwareId)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : workspaceText.softwareSaveFailed)
    } finally {
      setSoftwarePending(null)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting || activeConversationBusy) return
    // 新会话必须有指导书；已有会话可只发送后续要求。
    if (!activeConversationId && !guide) return setError(workspaceText.guideRequired)
    if (activeConversationId && guide) return setError(workspaceText.guideAlreadySet)
    if (!requirements.trim()) return setError(workspaceText.requirementsRequired)
    if (!deliverables.length && !/实验报告|论文|实验源文件|源文件/.test(requirements)) return setError(workspaceText.deliverablesRequired)

    const messageText = requirements.trim()
    const submittedGuide = guide
    const submittedReportTemplate = reportTemplate
    const submittedAttachments = attachments
    const submittedOtherFiles = otherFiles
    const submittedConversationId = activeConversationId
    const optimisticId = submittedConversationId ?? `pending-${crypto.randomUUID()}`
    const optimisticMessageId = `pending-${crypto.randomUUID()}`
    const requestId = crypto.randomUUID()
    const now = new Date().toISOString()
    const optimisticMessage = {
      id: optimisticMessageId, created_at: now, requirements: messageText,
      guide_name: submittedGuide?.name ?? null,
      report_template_name: submittedReportTemplate?.name ?? null,
      attachment_names: submittedAttachments.map((file) => file.name),
      other_file_names: submittedOtherFiles.map((file) => file.name),
      pending: true, request_id: requestId,
    }
    if (submittedConversationId) {
      setConversations((current) => current.map((item) => item.id === submittedConversationId
        ? { ...item, messages: [...item.messages, optimisticMessage] } : item))
    } else {
      setConversations((current) => [...current, {
        id: optimisticId, title: messageText.slice(0, 60), created_at: now, messages: [optimisticMessage],
      }])
      activeConversationIdRef.current = optimisticId
      setActiveConversationId(optimisticId)
    }
    hasChosenConversation.current = true
    setRequirements('')
    setGuide(null)
    setReportTemplate(null)
    setAttachments([])
    setOtherFiles([])
    setSubmitting(true)
    setError('')
    try {
      const saved = await createTask({
        requirements: messageText, software_id: selectedSoftware, deliverables,
        conversation_id: submittedConversationId, guide: submittedGuide, report_template: submittedReportTemplate, attachments: submittedAttachments, other_files: submittedOtherFiles, request_id: requestId,
      })
      // 服务端返回完整会话，用它替换本地副本，保持标题和消息顺序一致。
      setConversations((current) => [...current.filter((item) => item.id !== optimisticId && item.id !== saved.id), saved]
        .sort((first, second) => first.created_at.localeCompare(second.created_at)))
      if (activeConversationIdRef.current === optimisticId) activeConversationIdRef.current = saved.id
      setActiveConversationId((current) => current === optimisticId ? saved.id : current)
    } catch (cause) {
      setConversations((current) => submittedConversationId
        ? current.map((item) => item.id === optimisticId
          ? { ...item, messages: item.messages.filter((message) => message.id !== optimisticMessageId) } : item)
        : current.filter((item) => item.id !== optimisticId))
      if (activeConversationIdRef.current === optimisticId) {
        if (!submittedConversationId) {
          activeConversationIdRef.current = null
          setActiveConversationId(null)
        }
        setRequirements((current) => current || messageText)
        if (!submittedConversationId) setGuide((current) => current ?? submittedGuide)
        setReportTemplate((current) => current ?? submittedReportTemplate)
        setAttachments((current) => current.length ? current : submittedAttachments)
        setOtherFiles((current) => current.length ? current : submittedOtherFiles)
      }
      setError(cause instanceof Error ? cause.message : workspaceText.draftSaveFailed)
    } finally {
      setSubmitting(false)
    }
  }

  async function saveProfile(update: UserUpdate, avatar: File | null) {
    if (avatar) await uploadAvatar(avatar)
    const saved = await updateMe(update)
    onUserChange?.(saved)
    setEditingProfile(false)
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand__mark"><Sparkles size={22} fill="currentColor" /></div><div><strong>同学</strong><span>实验工作台</span></div></div>
        <div className="sidebar__group-label">工作空间</div>
        <nav className="sidebar__nav" aria-label="主导航">
          <a href="#/" className={`nav-item ${view === 'workspace' ? 'nav-item--active' : ''}`}><LayoutDashboard size={19} />实验工作台{view === 'workspace' && <span className="nav-item__dot" />}</a>
          <a href="#/solve" className={`nav-item ${view === 'solve' ? 'nav-item--active' : ''}`}><Sparkles size={19} />解题{view === 'solve' && <span className="nav-item__dot" />}</a>
          <a href="#/roommate" className={`nav-item ${view === 'roommate' ? 'nav-item--active' : ''}`} aria-current={view === 'roommate' ? 'page' : undefined}><MessageSquare size={19} />室友{view === 'roommate' && <span className="nav-item__dot" />}</a>
          {user?.identity === 1 && <a href="#/users" className={`nav-item ${view === 'users' ? 'nav-item--active' : ''}`}><Users size={19} />用户列表</a>}
          {isAdmin && <a href="#/mcp" className={`nav-item ${view === 'mcp' ? 'nav-item--active' : ''}`} aria-current={view === 'mcp' ? 'page' : undefined}><Plug size={19} />MCP 服务</a>}
          <a href="#/projects" className={`nav-item ${view === 'projects' ? 'nav-item--active' : ''}`}><FolderOpen size={19} />我的项目</a>
          <span className="nav-item nav-item--muted"><FileText size={19} />模板中心</span>
        </nav>
        <div className="sidebar__bottom"><a href="#/settings" className={`nav-item ${view === 'settings' ? 'nav-item--active' : ''}`}><Settings2 size={19} />{settingsText.title}</a>{user && <div className="sidebar__profile" ref={profileMenuRef}><button className="sidebar__avatar" type="button" aria-label="用户头像" aria-expanded={profileMenuOpen} onClick={() => setProfileMenuOpen((open) => !open)}>{user.image ? <img src={assetUrl(user.image)} alt="" /> : user.user_name.slice(0, 1).toUpperCase()}</button><span>{user.user_name}</span>{profileMenuOpen && <div className="sidebar__profile-menu"><button type="button" onClick={() => { setEditingProfile(true); setProfileMenuOpen(false) }}>个人信息</button><button type="button" onClick={onLogout}>退出登录</button></div>}</div>}<div className="sidebar__version">同学工作台 <span>v0.2</span></div></div>
      </aside>

      <main className={`main-content${view === 'roommate' ? ' main-content--roommate' : ''}`}>
        <header className="topbar">
          <div className="topbar__location"><div className="breadcrumb">{view === 'settings' ? settingsText.title : view === 'projects' ? '我的项目' : view === 'users' ? '用户列表' : view === 'roommate' ? '室友' : view === 'solve' ? '解题' : view === 'mcp' ? 'MCP 服务' : '实验工作台'} <span>/</span> <strong title={view === 'workspace' ? activeConversation?.title : undefined}>{view === 'settings' ? settingsText[settingsSection ?? 'modules'] : view === 'projects' ? projectId ? '项目文件' : '项目列表' : view === 'roommate' ? '对话' : view === 'solve' ? '题目对话' : view === 'mcp' ? '只读工具' : activeConversation?.title ?? '新建任务'}</strong></div><div className={`service-pill service-pill--${serviceState}`}><span className="service-pill__dot" />{serviceState === 'ready' ? '本地服务已连接' : serviceState === 'loading' ? '正在连接本地服务' : '本地服务未连接'}</div></div>
          {view === 'workspace' && <><div className="topbar__guide" aria-label="指导文件"><span>指导文件</span>{activeConversationId
            ? <span className="topbar__guide-name" title={savedGuideName ?? '指导文件已固定'}><FileText size={16} />{savedGuideName ?? '指导文件已固定'}</span>
            : <FileDropzone accept="application/pdf,.pdf" onFiles={addGuide} file={guide} onRemove={() => setGuide(null)} icon={<CloudUpload size={18} />} title="上传 PDF" hint="最大 120 MB" />}</div>
          <div className="topbar__software"><span>实验软件</span><SoftwarePicker items={software} selectedId={selectedSoftware} pendingId={softwarePending} onSelect={handleSoftwareSelect} /></div></>}
        </header>
        <nav className="mobile-module-nav" aria-label="模块导航"><a href="#/" aria-current={view === 'workspace' ? 'page' : undefined}>实验工作台</a><a href="#/solve" aria-current={view === 'solve' ? 'page' : undefined}>解题</a><a href="#/roommate" aria-current={view === 'roommate' ? 'page' : undefined}>室友</a>{isAdmin && <a href="#/mcp" aria-current={view === 'mcp' ? 'page' : undefined}>MCP 服务</a>}<a href="#/settings" aria-current={view === 'settings' ? 'page' : undefined}>设置</a></nav>
        {view === 'users' && user ? <UserList currentUser={user} /> : view === 'projects' ? <ProjectPage projectId={projectId} /> : view === 'solve' ? <SolutionPage /> : view === 'roommate' ? <RoommatePage /> : view === 'mcp' ? <McpPage /> : view === 'settings' ? <SettingsPage section={settingsSection} fontSize={fontSize} onFontSizeChange={onFontSizeChange} /> : <div className="content-layout">
        <div className="page-wrap">
          <section className="conversation" aria-label="对话记录" ref={conversationRef}>
            {messages.map((message) => <Fragment key={message.id}>
              <article className="conversation__item conversation__item--user" aria-label="用户消息">
                <p className="conversation__text">{message.requirements}</p>
                {(message.guide_name || message.report_template_name || message.attachment_names.length > 0 || message.other_file_names?.length || message.report_files?.length) && <div className="conversation__files">
                  {message.guide_name && <span className="conversation__file" title={message.guide_name}><FileText size={14} /><span>指导文件 · {message.guide_name}</span></span>}
                  {message.report_template_name && <span className="conversation__file" title={message.report_template_name}><FileText size={14} /><span>报告模板 · {message.report_template_name}</span></span>}
                  {message.attachment_names.map((name, index) => <span className="conversation__file" title={name} key={`${name}-${index}`}><Paperclip size={14} /><span>任务图片 · {name}</span></span>)}
                  {message.other_file_names?.map((name, index) => <span className="conversation__file" title={name} key={`${name}-${index}`}><Paperclip size={14} /><span>其他文件 · {name}</span></span>)}
                  {message.report_files?.map((name) => <button className="conversation__file conversation__download" type="button" key={name} onClick={() => downloadReport(activeConversation!.id, message.id, name).catch((cause) => setError(cause instanceof Error ? cause.message : workspaceText.reportDownloadFailed))}><FileText size={14} /><span>下载报告 · {name}</span></button>)}
                </div>}
                <span className="conversation__status">{message.pending || message.status === 'processing' ? <LoaderCircle size={14} className="spin" /> : <CheckCircle2 size={14} />}{message.pending || message.status === 'processing' ? '正在处理' : message.status === 'answered' ? '已回复' : message.status === 'error' ? '处理失败' : '草稿已保存'}</span>
              </article>
              {(message.reply || message.pending || message.status === 'processing') && <div className="conversation__assistant-wrap">{(message.pending || message.status === 'processing' || message.processing_seconds != null) && <span className="conversation__elapsed">已处理 {formatElapsed(message.pending || message.status === 'processing' ? elapsedSeconds : message.processing_seconds ?? 0)}</span>}<article className={`conversation__item conversation__item--assistant ${message.status === 'error' ? 'conversation__item--error' : ''}`} aria-label="助手回复">{message.reply ? <p className="conversation__text">{message.reply}</p> : <p className="conversation__progress" role="status"><LoaderCircle size={14} className="spin" />{progressStage || '正在处理'}</p>}</article></div>}
            </Fragment>)}
          </section>
          <form className="workspace-grid" onSubmit={handleSubmit}>
            <section className="panel task-panel" aria-labelledby="task-title">
              <div className="panel__heading"><div className="section-icon section-icon--violet"><Paperclip size={19} /></div><h2 id="task-title">任务图片</h2><span className="optional">选填</span></div>
              <FileDropzone accept="application/pdf,image/*,.pdf" multiple onFiles={addAttachments} file={attachments[0]} onRemove={() => setAttachments((current) => current.slice(1))} icon={<Plus size={20} />} title="添加图片或 PDF" hint="可多选" />
              {attachments.length > 1 && <div className="attachment-list">{attachments.slice(1).map((file, index) => <div className="attachment-item" key={`${file.name}-${index}`}><span className="attachment-item__icon">{file.name.toLowerCase().endsWith('.pdf') ? <FileText size={16} /> : <FileImage size={16} />}</span><span>{file.name}</span><small>{formatSize(file.size)}</small><button type="button" aria-label={`移除 ${file.name}`} onClick={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index + 1))}><X size={15} /></button></div>)}</div>}
              <div className="task-panel__other"><FileDropzone accept={otherFileExtensions.join(',')} multiple onFiles={addOtherFiles} file={otherFiles[0]} onRemove={() => setOtherFiles((current) => current.slice(1))} icon={<Plus size={20} />} title="其他文件 +" hint="实验素材，可多选" />
                {otherFiles.length > 1 && <div className="attachment-list">{otherFiles.slice(1).map((file, index) => <div className="attachment-item" key={`${file.name}-${index}`}><span className="attachment-item__icon"><FileText size={16} /></span><span>{file.name}</span><small>{formatSize(file.size)}</small><button type="button" aria-label={`移除 ${file.name}`} onClick={() => setOtherFiles((current) => current.filter((_, itemIndex) => itemIndex !== index + 1))}><X size={15} /></button></div>)}</div>}</div>
            </section>

            <section className="panel requirements-panel" aria-labelledby="requirements-title">
              <div className="panel__heading requirements-heading"><div className="section-icon section-icon--orange"><Sparkles size={19} /></div><h2 id="requirements-title">用户输入</h2><span className="required">必填</span><div className="deliverables"><span className="deliverables__label">交付成果</span><div className="deliverables__options">{deliverables.includes('report') && <label className="deliverable deliverable--template" title={reportTemplate?.name ?? '上传 DOC 或 DOCX 实验报告模板'}><FileText size={15} /><span>{reportTemplate?.name ?? '上传报告模板'}</span><input aria-label="上传实验报告模板" type="file" accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => { if (event.target.files?.length) addReportTemplate(Array.from(event.target.files)); event.target.value = '' }} /></label>}{deliverableOptions.map(({ id, label, icon: Icon }) => <button className={`deliverable ${id === 'report' ? 'deliverable--report' : ''} ${deliverables.includes(id) ? 'deliverable--selected' : ''}`} key={id} type="button" aria-pressed={deliverables.includes(id)} onClick={() => toggleDeliverable(id)}><Icon size={15} />{label}{deliverables.includes(id) && <Check size={13} />}</button>)}</div></div></div>
              <div className="composer"><textarea value={requirements} onChange={(event) => setRequirements(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!submitting) event.currentTarget.form?.requestSubmit() } }} aria-label="实验要求" placeholder="输入实验要求…" rows={3} />
                <button className="primary-button" type="submit" disabled={submitting || activeConversationBusy || serviceState !== 'ready'}>{submitting ? <LoaderCircle size={17} className="spin" /> : <Send size={17} />}发送</button>
              </div>
            </section>

            {error && <div className="message message--error" role="alert">{error}</div>}
          </form>
        </div>
        <aside className="history-sidebar" aria-labelledby="history-title">
          <div className="history-sidebar__heading"><h2 id="history-title"><History size={17} />历史对话信息</h2><span>{conversations.length}</span></div>
          <button className="history-sidebar__new" type="button" onClick={() => chooseConversation(null)}><Plus size={17} />新增会话</button>
          {conversations.length ? <nav className="history-list" aria-label="历史任务">{[...conversations].reverse().map((conversation) => <div className="history-list__row" key={conversation.id}>
            <button type="button" className={`history-list__item ${conversation.id === activeConversationId ? 'history-list__item--active' : ''}`} aria-pressed={conversation.id === activeConversationId} onClick={() => chooseConversation(conversation.id)}>
              <span className="history-list__icon"><History size={15} /></span><span className="history-list__title">{conversation.title}</span>
            </button>
            <button className="history-list__delete" type="button" aria-label={`归档会话 ${conversation.title}`} title={`归档会话 ${conversation.title}`} disabled={deletingConversationId === conversation.id || conversation.id.startsWith('pending-') || conversation.messages.some((message) => message.status === 'processing')} onClick={() => removeConversation(conversation.id, conversation.title)}><Trash2 size={15} /></button>
            <span className="history-list__date" role="tooltip">创建于 {new Date(conversation.created_at).toLocaleString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
          </div>)}</nav> : <p className="history-sidebar__empty">{historyError ? '历史记录暂不可用' : '暂无历史对话'}</p>}
        </aside>
        </div>}
      </main>
      {editingProfile && user && <UserEditor user={user} isAdmin={false} onSave={saveProfile} onClose={() => setEditingProfile(false)} />}
    </div>
  )
}
