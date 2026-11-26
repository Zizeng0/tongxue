import { Fragment, useRef, type DragEvent, type ReactNode } from 'react'
import { FileImage, FileText, Plus, X } from 'lucide-react'

interface FileDropzoneProps {
  accept: string
  multiple?: boolean
  onFiles: (files: File[]) => void
  file?: File | null
  files?: File[]
  onRemove?: () => void
  onRemoveFile?: (index: number) => void
  icon: ReactNode
  title: string
  hint: string
}

export default function FileDropzone({ accept, multiple = false, onFiles, file, files, onRemove, onRemoveFile, icon, title, hint }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const selected = files ?? (file ? [file] : [])

  function receiveFiles(files: FileList | null) {
    if (files?.length) onFiles(Array.from(files))
    // 清空原生输入值，用户再次选择同名文件时仍能触发 change。
    if (inputRef.current) inputRef.current.value = ''
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    receiveFiles(event.dataTransfer.files)
  }

  const entries = selected.map((item, index) => {
    const entry = <>
      <span className="dropzone__icon">{/\.(?:png|jpe?g|webp|gif|bmp|svg)$/i.test(item.name) ? <FileImage size={16} /> : <FileText size={16} />}</span>
      <span className="dropzone__filename" title={item.name}>{item.name}</span>
      <button className="dropzone__remove" type="button" aria-label={`移除 ${item.name}`}
        onClick={files ? () => onRemoveFile?.(index) : onRemove}><X size={15} /></button>
    </>
    return files ? <span className="dropzone__entry" key={index}>{entry}</span> : <Fragment key={index}>{entry}</Fragment>
  })

  return (
    <div className={`dropzone${files && selected.length ? ' dropzone--multiple' : ''}`} onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}>
      {selected.length ? <>
        {files ? <div className="dropzone__files">{entries}</div> : entries}
        <button className="dropzone__add" type="button" aria-label="添加文件" onClick={() => inputRef.current?.click()}><Plus size={17} /></button>
      </> : <button className="dropzone__empty" type="button" aria-label={title} onClick={() => inputRef.current?.click()}>
        <span className="dropzone__icon">{icon}</span><span className="dropzone__title">{title}</span><span className="dropzone__hint">{hint}</span>
      </button>}
      <input ref={inputRef} type="file" accept={accept} multiple={multiple} className="visually-hidden" tabIndex={-1} onChange={(event) => receiveFiles(event.target.files)} />
    </div>
  )
}
