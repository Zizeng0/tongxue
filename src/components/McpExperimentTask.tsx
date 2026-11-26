import { useEffect, useState, type FormEvent } from 'react'
import { FlaskConical } from 'lucide-react'
import { getMcpExperiment, getProjects, submitMcpExperiment, type McpExperimentTask, type ProjectSummary } from '../api'

const deliverableLabels = { report: '实验报告', paper: '论文', code: '源文件' }
const statusLabels = {
  queued: '排队中', running: '执行中', answered: '工作流已完成', error: '执行失败',
  draft: '草稿', interrupted: '任务已中断',
}

export default function McpExperimentTaskPanel() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [projectId, setProjectId] = useState('')
  const [requirements, setRequirements] = useState('')
  const [deliverables, setDeliverables] = useState<string[]>(['report'])
  const [confirmed, setConfirmed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [task, setTask] = useState<McpExperimentTask | null>(null)
  const [error, setError] = useState('')
  const [retryId, setRetryId] = useState('')
  const project = projects?.find((item) => item.id === projectId)
  const running = task?.status === 'queued' || task?.status === 'running'
  const runningId = running ? task.request_id : undefined
  const runningProject = running ? task.conversation_id : undefined

  useEffect(() => {
    let active = true
    getProjects().then(({ items }) => {
      if (active) setProjects(items.filter((item) => item.kind === 'task' && item.software_id))
    }).catch((cause: Error) => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!runningId || !runningProject) return
    const requestId = runningId
    const conversationId = runningProject
    const controller = new AbortController()
    let timeout: number
    async function refresh() {
      try {
        const current = await getMcpExperiment(conversationId, requestId, controller.signal)
        if (controller.signal.aborted) return
        setError('')
        setTask(current)
        if (current.status !== 'queued' && current.status !== 'running') return
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : '无法查询实验状态。')
      }
      timeout = window.setTimeout(refresh, 2500)
    }
    timeout = window.setTimeout(refresh, 1500)
    return () => { window.clearTimeout(timeout); controller.abort() }
  }, [runningId, runningProject])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!project || !confirmed || running || submitting) return
    const requestId = retryId || crypto.randomUUID()
    setRetryId(requestId)
    setSubmitting(true)
    setError('')
    setTask(null)
    try {
      setTask(await submitMcpExperiment(project.id, project.software_id, requirements.trim(), deliverables, requestId))
      setRetryId('')
      setConfirmed(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '实验任务提交失败。')
    } finally {
      setSubmitting(false)
    }
  }

  return <section className="mcp-tool" aria-labelledby="mcp-experiment">
    <h2 id="mcp-experiment"><FlaskConical size={18} aria-hidden="true" />提交实验任务</h2>
    <p className="mcp-range-hint">先在实验工作台建立会话并上传 PDF 指导书。提交后后台执行，可在「我的项目」查看产物。</p>
    <form onSubmit={submit} onChange={() => setRetryId('')}>
      <fieldset className="mcp-form mcp-form--artifact" aria-labelledby="mcp-experiment" disabled={submitting || running}>
        <label>实验项目<select required value={projectId} onChange={(event) => setProjectId(event.target.value)}>
          <option value="">{projects ? (projects.length ? '请选择项目' : '暂无实验项目') : '正在加载实验项目…'}</option>
          {projects?.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.software_id}</option>)}
        </select></label>
        <label>实验要求<textarea required rows={3} maxLength={16000} value={requirements}
          onChange={(event) => setRequirements(event.target.value)} /></label>
        <div className="mcp-experiment-options" role="group" aria-label="交付项">
          {Object.entries(deliverableLabels).map(([key, label]) => <label key={key}>
            <input type="checkbox" checked={deliverables.includes(key)} onChange={(event) => {
              setDeliverables(event.target.checked ? [...deliverables, key] : deliverables.filter((item) => item !== key))
            }} />{label}
          </label>)}
        </div>
        <label className="mcp-experiment-confirm">
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          确认在本机后台执行此实验工作流
        </label>
        <button className="auth-card__submit" type="submit"
          disabled={!confirmed || !project || !requirements.trim() || !deliverables.length}>
          {submitting ? '正在提交…' : '提交任务'}
        </button>
      </fieldset>
    </form>
    {error && <p className="auth-card__error" role="alert">{error}</p>}
    {task && <div className="mcp-results" aria-live="polite">
      <h3>{statusLabels[task.status]}</h3>
      {task.stage && <p>{task.stage}</p>}
      {task.reply && <p className="mcp-page-text">{task.reply}</p>}
      <p><a href={`#/projects/${encodeURIComponent(task.conversation_id)}`}>查看实验项目</a></p>
    </div>}
  </section>
}
