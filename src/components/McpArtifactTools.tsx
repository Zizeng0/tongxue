import { useState, type FormEvent } from 'react'
import { FileDown } from 'lucide-react'
import { runMcpArtifact, type McpArtifactTool } from '../api'

const tools = {
  render_presentation: {
    label: '生成 PPTX',
    hint: '用已编排的幻灯片内容生成可编辑 PPTX，含封面共 2–20 页。',
    example: { deck: { title: '压力实验', slides: [{ title: '测量结果', points: ['实验压力为 12 kPa'] }] } },
  },
  render_thesis: {
    label: '导出论文 Word',
    hint: '用已写好的标题和正文生成 Word，段落可用来源编号关联书目。',
    example: {
      manuscript: { title: '校园雨水回收研究', sections: [
        { title: '研究方法', paragraphs: [{ text: '按不同降雨强度比较集水效率。' }] },
      ] },
    },
  },
  plot_discrete_sequence: {
    label: '绘制离散序列',
    hint: '输入 1–256 个有限实数样本，生成 PNG 茎状图。',
    example: { samples: [0, 1, 2, 1, 0], start_index: -2, title: '离散序列' },
  },
} satisfies Record<McpArtifactTool, { label: string; hint: string; example: Record<string, unknown> }>

function exampleFor(tool: McpArtifactTool) {
  return JSON.stringify(tools[tool].example, null, 2)
}

export default function McpArtifactTools() {
  const [tool, setTool] = useState<McpArtifactTool>('render_presentation')
  const [parameters, setParameters] = useState(() => exampleFor('render_presentation'))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<Awaited<ReturnType<typeof runMcpArtifact>> | null>(null)

  function chooseTool(next: McpArtifactTool) {
    setTool(next)
    setParameters(exampleFor(next))
    setError('')
    setResult(null)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setResult(null)
    let parsed: unknown
    try {
      parsed = JSON.parse(parameters)
    } catch {
      setError('请输入有效的 JSON 对象。')
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('请输入有效的 JSON 对象。')
      return
    }
    setPending(true)
    try {
      setResult(await runMcpArtifact(tool, parsed as Record<string, unknown>))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '生成文件失败。')
    } finally {
      setPending(false)
    }
  }

  return <section className="mcp-tool" aria-labelledby="mcp-artifacts">
    <h2 id="mcp-artifacts"><FileDown size={18} aria-hidden="true" />生成与绘图工具</h2>
    <form onSubmit={submit}>
      <fieldset className="mcp-form mcp-form--artifact" aria-labelledby="mcp-artifacts" disabled={pending}>
        <label>工具<select value={tool} onChange={(event) => chooseTool(event.target.value as McpArtifactTool)}>
          {Object.entries(tools).map(([id, { label }]) => <option key={id} value={id}>{label}</option>)}
        </select></label>
        <p className="mcp-range-hint" id="mcp-artifact-help">{tools[tool].hint}</p>
        <label>参数（JSON）<textarea required rows={9} value={parameters} aria-describedby="mcp-artifact-help"
          onChange={(event) => setParameters(event.target.value)} spellCheck={false} /></label>
        <button className="auth-card__submit" type="submit">{pending ? '正在生成…' : '运行工具'}</button>
      </fieldset>
    </form>
    {error && <p className="auth-card__error" role="alert">{error}</p>}
    <div className="mcp-results" aria-live="polite">
      {result && <>
        {result.image && <img className="mcp-pdf-preview" src={result.href} alt="离散序列图" />}
        <a href={result.href} download={result.filename}>下载 {result.filename}</a>
      </>}
    </div>
  </section>
}
