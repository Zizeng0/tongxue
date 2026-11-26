import { useEffect, useState, type FormEvent } from 'react'
import { FileSearch, Search } from 'lucide-react'
import {
  getProject, getProjects, previewMcpPdfPage, readMcpPdf, searchMcpPapers,
  type McpPaper, type McpPdfResult, type ProjectFile, type ProjectSummary,
} from '../api'
import McpArtifactTools from './McpArtifactTools'
import McpExperimentTaskPanel from './McpExperimentTask'

const pdfPagesPerScreen = 5
const maxPdfPagesPerRead = 50

function LiteratureSearch() {
  const [query, setQuery] = useState('')
  const [papers, setPapers] = useState<McpPaper[] | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    setError('')
    setPapers(null)
    try {
      setPapers((await searchMcpPapers(query.trim())).papers)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '文献检索失败。')
    } finally {
      setPending(false)
    }
  }

  return <section className="mcp-tool" aria-labelledby="mcp-literature">
    <h2 id="mcp-literature"><Search size={18} aria-hidden="true" />文献检索</h2>
    <form className="mcp-form" onSubmit={submit}>
      <label>检索关键词<input required maxLength={250} value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <button className="auth-card__submit" disabled={pending || !query.trim()} type="submit">{pending ? '正在检索…' : '检索 Crossref'}</button>
    </form>
    {error && <p className="auth-card__error" role="alert">{error}</p>}
    <div className="mcp-results" aria-live="polite">
      {papers?.length === 0 && <p>未找到匹配的文献。</p>}
      {papers?.map((paper) => <article key={paper.doi}>
        <h3><a href={paper.url} target="_blank" rel="noreferrer">{paper.title}</a></h3>
        <p>{[paper.authors, paper.journal, paper.year].filter(Boolean).join(' · ')}</p>
        <p>DOI {paper.doi}</p>
        {paper.abstract && <p>{paper.abstract}</p>}
      </article>)}
    </div>
  </section>
}

function PdfPagePreview({ projectId, filePath, page }: { projectId: string; filePath: string; page: number }) {
  const [image, setImage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    previewMcpPdfPage(projectId, filePath, page, controller.signal).then((url) => {
      if (!controller.signal.aborted) setImage(url)
    }).catch((cause) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '预览加载失败。')
    })
    return () => controller.abort()
  }, [projectId, filePath, page])

  if (error) return <p role="status">{error}</p>
  if (!image) return <p role="status">正在加载第 {page} 页预览…</p>
  return <img className="mcp-pdf-preview" src={image} alt={`PDF 第 ${page} 页预览`} />
}

function PdfReader() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [projectId, setProjectId] = useState('')
  const [files, setFiles] = useState<ProjectFile[] | null>(null)
  const [filePath, setFilePath] = useState('')
  const [start, setStart] = useState('1')
  const [end, setEnd] = useState('1')
  const [result, setResult] = useState<McpPdfResult | null>(null)
  const [pending, setPending] = useState(false)
  const [projectError, setProjectError] = useState('')
  const [readError, setReadError] = useState('')
  const [screen, setScreen] = useState(0)
  const screenCount = result ? Math.ceil(result.pages.length / pdfPagesPerScreen) : 0
  const visiblePages = result?.pages.slice(screen * pdfPagesPerScreen, (screen + 1) * pdfPagesPerScreen) ?? []

  useEffect(() => {
    let active = true
    getProjects().then(({ items }) => {
      if (active) setProjects(items)
    }).catch((cause: Error) => {
      if (active) setProjectError(cause.message)
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!projectId) return
    let active = true
    getProject(projectId).then((project) => {
      if (!active) return
      const inputs = [...(project.guide ? [project.guide] : []), ...project.messages.flatMap((turn) => turn.inputs)]
      const pdfs = inputs.filter((file) => file.path.toLowerCase().endsWith('.pdf'))
      const available = [...new Map(pdfs.map((file) => [file.path, file])).values()]
      setFiles(available)
      if (available.length === 1) setFilePath(available[0].path)
    }).catch((cause: Error) => {
      if (active) setProjectError(cause.message)
    })
    return () => { active = false }
  }, [projectId])

  function chooseProject(id: string) {
    setProjectId(id)
    setFiles(null)
    chooseFile('')
    setProjectError('')
  }

  function chooseFile(path: string) {
    setFilePath(path)
    setResult(null)
    setReadError('')
    setStart('1')
    setEnd('1')
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const firstPage = Number(start)
    const lastPage = Number(end)
    setResult(null)
    setScreen(0)
    if (lastPage < firstPage) {
      setReadError('结束页不能早于起始页。')
      return
    }
    if (lastPage - firstPage + 1 > maxPdfPagesPerRead) {
      setReadError(`一次最多读取 ${maxPdfPagesPerRead} 页，请缩小页码范围。`)
      return
    }
    setPending(true)
    setReadError('')
    try {
      setResult(await readMcpPdf(projectId, filePath, firstPage, lastPage))
    } catch (cause) {
      setReadError(cause instanceof Error ? cause.message : 'PDF 读取失败。')
    } finally {
      setPending(false)
    }
  }

  return <section className="mcp-tool" aria-labelledby="mcp-pdf">
    <h2 id="mcp-pdf"><FileSearch size={18} aria-hidden="true" />读取项目 PDF</h2>
    <form onSubmit={submit} aria-describedby="mcp-pdf-range">
      <fieldset className="mcp-form mcp-form--pdf" aria-label="PDF 文件和页码" disabled={pending}>
        <label>项目<select required value={projectId} onChange={(event) => chooseProject(event.target.value)} disabled={!projects?.length}>
          <option value="">{projects ? (projects.length ? '请选择项目' : '暂无项目') : (projectError ? '项目加载失败' : '正在加载项目…')}</option>
          {projects?.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
        </select></label>
        <label>PDF 文件<select required value={filePath} onChange={(event) => chooseFile(event.target.value)} disabled={!files?.length}>
          <option value="">{files ? (files.length ? '请选择 PDF' : '此项目没有 PDF 附件') : (projectId && !projectError ? '正在加载文件…' : '请选择项目')}</option>
          {files?.map((file) => <option key={file.path} value={file.path}>{file.name}</option>)}
        </select></label>
        <label>起始页<input required type="number" min={1} value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <label>结束页<input required type="number" min={1} value={end} onChange={(event) => setEnd(event.target.value)} /></label>
        <button className="auth-card__submit" disabled={!filePath} type="submit">{pending ? '正在读取…' : '读取页码'}</button>
      </fieldset>
    </form>
    <p className="mcp-range-hint" id="mcp-pdf-range">每次最多读取 {maxPdfPagesPerRead} 页，每屏显示 {pdfPagesPerScreen} 页。</p>
    {(projectError || readError) && <p className="auth-card__error" role="alert">{projectError || readError}</p>}
    {screenCount > 1 && <nav className="mcp-pagination" aria-label="PDF 结果分页">
      <button type="button" disabled={screen === 0} onClick={() => setScreen(screen - 1)}>上一页</button>
      <span aria-live="polite">第 {screen + 1} / {screenCount} 屏 · 显示 PDF 第 {visiblePages[0].page}–{visiblePages.at(-1)?.page} 页</span>
      <button type="button" disabled={screen === screenCount - 1} onClick={() => setScreen(screen + 1)}>下一页</button>
    </nav>}
    <div className="mcp-results" aria-live="polite">
      {result?.truncated && <p>部分页面的文字已截断，可查看整页预览。</p>}
      {visiblePages.map((page) => <article key={page.page}>
        <h3>第 {page.page} 页</h3>
        <PdfPagePreview projectId={projectId} filePath={filePath} page={page.page} />
        <p className="mcp-page-text">{page.source === 'no_text_layer' ? '本页没有可读取的文字层。' : page.text}</p>
      </article>)}
    </div>
  </section>
}

export default function McpPage() {
  return <section className="projects-page mcp-page" aria-labelledby="mcp-title">
    <div className="projects-page__heading"><h1 id="mcp-title">MCP 服务</h1></div>
    <LiteratureSearch />
    <PdfReader />
    <McpArtifactTools />
    <McpExperimentTaskPanel />
  </section>
}
