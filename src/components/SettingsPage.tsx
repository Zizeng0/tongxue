import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ChevronDown, ChevronRight, Cpu, SlidersHorizontal, Type } from 'lucide-react'
import { getModelSettings, getOutputSettings, previewModelConnection, updateModelSettings, updateOutputSettings, type ModelConnection, type ModelRole, type ModelSettings, type OutputDetail } from '../api'
import { settingsText as copy } from './constants'
import { defaultFontSize, fontSizes, parseFontSize } from '../utils/fontSize'

const roles = ['text', 'vision'] as const

/** 使用个人信息的弹窗布局验证新连接，每次打开都从空密钥开始。 */
function ConnectionEditor({ role, onClose, onSave }: {
  role: ModelRole
  onClose: () => void
  onSave: (connection: ModelConnection, options: string[]) => void
}) {
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    setError('')
    const connection = { base_url: baseUrl.trim(), api_key: apiKey.trim() }
    try {
      const options = await previewModelConnection(role, connection)
      if (!options.length) {
        setError(copy.emptyConnection(copy[role].name))
        return
      }
      onSave(connection, options)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : copy.connectionError)
    } finally {
      setPending(false)
    }
  }

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
    if (!pending && event.target === event.currentTarget) onClose()
  }}>
    <form className="user-dialog" role="dialog" aria-modal="true" aria-labelledby="model-connection-title" onSubmit={submit} onKeyDown={(event) => {
      if (!pending && event.key === 'Escape') onClose()
    }}>
      <h2 id="model-connection-title">{copy[role].name} · {copy.editConnection}</h2>
      <label>{copy.baseUrl}<input type="url" autoFocus value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} maxLength={2048} required disabled={pending} /></label>
      <label>{copy.apiKey}<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} maxLength={4096} required disabled={pending} /></label>
      {error && <p className="auth-card__error" role="alert">{error}</p>}
      <div className="user-dialog__actions"><button type="button" onClick={onClose} disabled={pending}>{copy.cancel}</button><button type="submit" disabled={pending}>{pending ? copy.checking : copy.save}</button></div>
    </form>
  </div>
}

/** 单独加载、编辑和提交一个模型类型的配置。 */
function ModelSettingsRow({ role, editingRole, onEdit }: {
  role: ModelRole
  editingRole: ModelRole | null
  onEdit: (role: ModelRole | null) => void
}) {
  const [settings, setSettings] = useState<ModelSettings | null>(null)
  const [selection, setSelection] = useState('')
  const [preview, setPreview] = useState<{ connection: ModelConnection; options: string[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [reload, setReload] = useState(0)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const connectionButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let active = true
    getModelSettings(role).then((result) => {
      if (!active) return
      setSettings(result)
      setSelection(result.current)
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : copy.loadError)
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [role, reload])

  const changed = preview || selection !== settings?.current
  const options = preview?.options ?? (settings ? [...new Set([settings.current, ...settings.options])] : [])

  function closeEditor() {
    onEdit(null)
    requestAnimationFrame(() => connectionButton.current?.focus())
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!settings || !changed || saving || !selection) return
    setSaving(true)
    setError('')
    setSaved(false)
    try {
      const { revision } = await updateModelSettings(role, settings.revision, selection, preview?.connection)
      setSettings({
        revision,
        current: selection,
        options: preview?.options ?? settings.options,
        error: preview ? null : settings.error,
      })
      setPreview(null)
      setSaved(true)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : copy.saveError)
    } finally {
      setSaving(false)
    }
  }

  function refresh() {
    setLoading(true)
    setSettings(null)
    setPreview(null)
    setError('')
    setSaved(false)
    setReload((value) => value + 1)
  }

  return <div className="model-settings__item">
    <form className="project-message__row model-settings__row" aria-label={copy[role].name} onSubmit={submit} inert={editingRole !== null}>
      <label htmlFor={`model-${role}`}>{copy[role].name}</label>
      <div className="model-settings__field">
        {loading && <p role="status">{copy.loading}</p>}
        {settings && <select id={`model-${role}`} value={selection} aria-describedby={`model-${role}-description`} required disabled={saving} onChange={(event) => {
          setSelection(event.target.value)
          setSaved(false)
        }}>
          {!selection && <option value="" disabled>{copy.select}</option>}
          {options.map((name) => <option value={name} key={name}>{name}</option>)}
        </select>}
        {!preview && settings?.error && <span className="auth-card__error" role="status">{settings.error}</span>}
        {error && <p className="auth-card__error" role="alert">{error}</p>}
        {saved && <p role="status">{copy.saved}</p>}
        {preview && <p role="status">{copy.pendingConnection}</p>}
        {!loading && (error || (!preview && settings?.error)) && <button className="project-file" type="button" disabled={saving} onClick={refresh}>{copy.reload}</button>}
      </div>
      <p id={`model-${role}-description`}>{copy[role].description}</p>
      {settings && <div className="model-settings__actions">
        <button className="model-settings__connection" ref={connectionButton} type="button" disabled={saving} onClick={() => onEdit(role)}>{copy.editConnection}</button>
        <button className="auth-card__submit" type="submit" disabled={!changed || saving || !selection}>{saving ? copy.saving : copy.submit}</button>
      </div>}
    </form>
    {editingRole === role && <ConnectionEditor role={role} onClose={closeEditor} onSave={(connection, available) => {
      setPreview({ connection, options: available })
      setSelection(available.includes(selection) ? selection : '')
      setError('')
      setSaved(false)
      closeEditor()
    }} />}
  </div>
}

function ModelSettingsForm() {
  const [editingRole, setEditingRole] = useState<ModelRole | null>(null)
  return <div className="project-message model-settings">
    {roles.map((role) => <ModelSettingsRow key={role} role={role} editingRole={editingRole} onEdit={setEditingRole} />)}
  </div>
}

function AppearanceSettings({ fontSize, onChange }: { fontSize: number; onChange?: (size: number) => void }) {
  const [draft, setDraft] = useState(String(fontSize))
  const [expanded, setExpanded] = useState(false)
  const toggle = useRef<HTMLButtonElement>(null)

  function applySize(value: string) {
    const size = parseFontSize(value)
    if (size !== null) onChange?.(size)
    setDraft(String(size ?? fontSize))
  }

  return <section className="project-message appearance-settings" aria-labelledby="font-size-title">
    <h2 id="font-size-title">字体大小</h2>
    <div className="font-setting">
      <label htmlFor="global-font-size">全局字体大小</label>
      <div className="font-setting__input" onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setExpanded(false)
      }} onKeyDown={(event) => {
        if (event.key === 'Escape' && expanded) {
          setExpanded(false)
          toggle.current?.focus()
        }
      }}>
        <div className="font-picker">
          <input id="global-font-size" inputMode="decimal" value={draft}
            title="输入 12–24，可保留一位小数，按回车或离开输入框后生效"
            onChange={(event) => setDraft(event.currentTarget.value)}
            onBlur={(event) => applySize(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                applySize(event.currentTarget.value)
                setExpanded(false)
              }
            }} />
          <button type="button" ref={toggle} aria-label="字号预设" aria-expanded={expanded}
            aria-controls="font-size-options" onClick={() => setExpanded((open) => !open)}><ChevronDown size={16} /></button>
          <div id="font-size-options" className="font-picker__options" role="group" aria-label="可选字号" hidden={!expanded}>
            {fontSizes.map((size) => <button key={size} type="button" aria-pressed={fontSize === size} onClick={() => {
              applySize(String(size))
              setExpanded(false)
              toggle.current?.focus()
            }}>{size}</button>)}
          </div>
        </div>
        <span aria-hidden="true">px</span>
      </div>
    </div>
  </section>
}

function OutputSettingsForm() {
  const [detail, setDetail] = useState<OutputDetail>()
  const [saved, setSaved] = useState<OutputDetail>()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    getOutputSettings().then((settings) => {
      if (active) {
        setSaved(settings.detail)
        setDetail(settings.detail)
      }
    }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '无法读取输出设置。') })
    return () => { active = false }
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!detail || detail === saved || saving) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const settings = await updateOutputSettings(detail)
      setSaved(settings.detail)
      setDetail(settings.detail)
      setNotice('输出设置已保存。')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法保存输出设置。')
    } finally {
      setSaving(false)
    }
  }

  return <form className="project-message appearance-settings output-settings" onSubmit={submit}>
    <div className="font-setting output-settings__row">
      <label htmlFor="output-detail">输出内容详略设置</label>
      <div className="output-settings__controls">
        <select id="output-detail" value={detail ?? ''} disabled={!detail || saving} aria-describedby="output-detail-description"
          onChange={(event) => { setDetail(event.currentTarget.value as OutputDetail); setNotice('') }}>
          {!detail && <option value="">读取设置</option>}
          <option value="detailed">详细、丰富</option><option value="normal">正常</option><option value="brief">简略</option>
        </select>
        <button className="auth-card__submit" type="submit" disabled={!detail || detail === saved || saving}>{saving ? '正在保存…' : '保存'}</button>
      </div>
    </div>
    <p id="output-detail-description">适用于各模块的模型生成内容，本轮明确要求优先。</p>
    {detail === 'detailed' && <div className="output-settings__detail" aria-label="详细档的输出内容">
      <dl>
        <dt>PPT</dt><dd>安排更多内容层次，补充分页讲稿，并在允许时检索与主题相关的配图。</dd>
        <dt>论文</dt><dd>先确定研究问题、检索文献和组织框架，再撰写并审阅引文、论证与证据局限。</dd>
        <dt>实验报告</dt><dd>补充关键步骤、实验条件、结果依据与必要论证，按任务要求组织完整报告。</dd>
        <dt>解题过程</dt><dd>展开推导、适用条件和理由，必要时用图、对照或边界情况解释。</dd>
      </dl>
      <p><strong>详细档通常会增加 Token 消耗。</strong></p>
    </div>}
    {error && <p className="auth-card__error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </form>
}

export default function SettingsPage({ section, fontSize = defaultFontSize, onFontSizeChange }: {
  section?: 'models' | 'appearance' | 'output'
  fontSize?: number
  onFontSizeChange?: (size: number) => void
}) {
  return <section className="projects-page" aria-labelledby="settings-title">
    <div className="projects-page__heading">
      <h1 id="settings-title">{copy[section ?? 'title']}</h1>
      {section && <a href="#/settings" className="projects-page__back">{copy.back}</a>}
    </div>
    {section === 'models' ? <ModelSettingsForm /> : section === 'appearance' ? <AppearanceSettings fontSize={fontSize} onChange={onFontSizeChange} />
      : section === 'output' ? <OutputSettingsForm /> : <div className="projects-page__list">
      <a className="project-summary settings-module" href="#/settings/models"><Cpu size={18} /><strong>{copy.models}</strong><ChevronRight size={16} /></a>
      <a className="project-summary settings-module" href="#/settings/appearance"><Type size={18} /><strong>{copy.appearance}</strong><ChevronRight size={16} /></a>
      <a className="project-summary settings-module" href="#/settings/output"><SlidersHorizontal size={18} /><strong>{copy.output}</strong><ChevronRight size={16} /></a>
    </div>}
  </section>
}
