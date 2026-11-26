import { componentText } from './constants'
import { useEffect, useState } from 'react'
import { Download, FileArchive, FileCode2, FileImage, FileJson2, FileSpreadsheet, FileText, FolderOpen, Presentation } from 'lucide-react'
import { downloadProjectFile, getProject, getProjects, type ProjectDetail, type ProjectFile, type ProjectSummary } from '../api'
import { softwareIcons } from './SoftwarePicker'

const softwareNames: Record<string, string> = {
  modelsim: 'Modelsim SE-64 10.1c', quartus: 'Quartus II 13.1 (64-bit)',
  'packet-tracer': 'Cisco Packet Tracer', hfss: 'Ansys HFSS', python: 'PyCharm', pycharm: 'PyCharm',
}

const projectLabels = { task: '实验工作台', solution: '解题', roommate: '室友' }

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function fileIcon(file: ProjectFile, softwareId: string) {
  if (file.kind === 'code') return softwareIcons[softwareId as keyof typeof softwareIcons] ?? FileCode2
  const extension = file.name.split('.').at(-1)?.toLowerCase()
  if (file.kind === 'presentation' || extension === 'pptx') return Presentation
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'svg'].includes(extension ?? '')) return FileImage
  if (['csv', 'tsv', 'xls', 'xlsx'].includes(extension ?? '')) return FileSpreadsheet
  if (extension === 'json') return FileJson2
  if (['py', 'v', 'sv', 'vhd', 'vhdl'].includes(extension ?? '')) return FileCode2
  if (['zip', 'rar', '7z'].includes(extension ?? '')) return FileArchive
  return FileText
}

export default function ProjectPage({ projectId }: { projectId: string | null }) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setProject(null)
    const request = projectId ? getProject(projectId) : getProjects()
    request.then((result) => {
      if (!active) return
      if ('messages' in result) setProject(result)
      else setProjects(result.items)
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : componentText.projectLoadFailed)
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [projectId])

  function fileCard(file: ProjectFile) {
    const Icon = fileIcon(file, project?.software_id ?? '')
    return <button className="project-file" key={file.path} type="button" onClick={() => {
      if (projectId) downloadProjectFile(projectId, file).catch((cause) => setError(cause instanceof Error ? cause.message : componentText.projectDownloadFailed))
    }} title={`下载 ${file.name}`}><Icon size={17} /><span>{file.name}</span><small>{file.size < 1024 * 1024 ? `${Math.ceil(file.size / 1024)} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`}</small><Download size={15} /></button>
  }

  return <section className="projects-page" aria-label="我的项目">
    <div className="projects-page__heading"><div><h1>{project?.title ?? '我的项目'}</h1><p>{projectId ? '查看会话消息与文件' : '按会话查看输入文件和生成成果'}</p></div>{projectId && <a className="projects-page__back" href="#/projects">返回项目列表</a>}</div>
    {error && <p className="auth-card__error" role="alert">{error}</p>}
    {loading ? <p className="projects-page__empty">正在加载项目…</p> : projectId && project ? <>
      <div className="projects-page__summary"><span>{projectLabels[project.kind ?? 'task']}</span><span>创建于 {formatTime(project.created_at)}</span>{(project.kind ?? 'task') === 'task' && <span>实验软件：{softwareNames[project.software_id] ?? project.software_id ?? '未记录'}</span>}</div>
      {(project.kind ?? 'task') === 'task' && <section className="projects-page__guide"><h2>指导文件</h2>{project.guide ? fileCard(project.guide) : <p>暂无指导文件</p>}</section>}
      <div className="project-messages">{project.messages.map((message) => <div className="project-message" key={message.message_id}>
        <section className="project-message__row"><h2>输入</h2><div className="project-message__content"><p>{message.input_preview}</p><div className="project-message__files">{message.inputs.map(fileCard)}</div></div><time>{formatTime(message.created_at)}</time></section>
        <section className="project-message__row"><h2>输出</h2><div className="project-message__content">{message.outputs.length ? <div className="project-message__files">{message.outputs.map(fileCard)}</div> : <p>暂无输出文件</p>}</div></section>
      </div>)}</div>
    </> : !projectId ? <div className="projects-page__list">{projects.length ? projects.map((item) => <a className="project-summary" href={`#/projects/${encodeURIComponent(item.id)}`} key={item.id}><FolderOpen size={18} /><span className="project-summary__kind">{projectLabels[item.kind ?? 'task']}</span><strong>{item.title}</strong><time>{formatTime(item.created_at)}</time><span>{item.kind === 'roommate' ? '对话文件' : item.kind === 'solution' ? '生成图片' : softwareNames[item.software_id] ?? item.software_id ?? '未记录'}</span></a>) : <p className="projects-page__empty">暂无项目</p>}</div> : null}
  </section>
}
