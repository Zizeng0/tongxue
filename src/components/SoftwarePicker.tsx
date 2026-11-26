import { useEffect, useRef, useState } from 'react'
import { CircuitBoard, Boxes, Network, Radio, Code2, ChevronDown, Check, LoaderCircle } from 'lucide-react'
import type { Software } from '../types'

interface SoftwarePickerProps {
  items: Software[]
  selectedId: string
  pendingId: string | null
  onSelect: (softwareId: string) => void
}

export const softwareIcons = {
  modelsim: CircuitBoard,
  quartus: Boxes,
  'packet-tracer': Network,
  hfss: Radio,
  pycharm: Code2,
  python: Code2,
}

export default function SoftwarePicker({ items, selectedId, pendingId, onSelect }: SoftwarePickerProps) {
  const [open, setOpen] = useState(false)
  const pickerRef = useRef<HTMLDivElement>(null)
  const selected = items.find((item) => item.id === selectedId)
  const Icon = softwareIcons[selectedId as keyof typeof softwareIcons] ?? CircuitBoard

  useEffect(() => {
    if (!open) return
    // 仅菜单展开时监听外部点击，关闭后立即移除监听器。
    function closeOutside(event: PointerEvent) {
      if (!pickerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [open])

  return (
    <div className="software-picker" ref={pickerRef} onKeyDown={(event) => {
      if (event.key === 'Escape') setOpen(false)
    }}>
      <button className="software-picker__trigger" type="button" aria-label="实验软件" aria-expanded={open} aria-haspopup="listbox" onClick={() => setOpen((current) => !current)}>
        <span className="software-picker__icon"><Icon size={17} /></span>
        <span className="software-picker__name">{selected?.name ?? '选择实验软件'}</span>
        {pendingId ? <LoaderCircle className="spin" size={16} /> : <ChevronDown className={open ? 'software-picker__chevron--open' : ''} size={16} />}
      </button>
      {open && <div className="software-picker__menu" role="listbox" aria-label="实验软件选项">
        {items.map((item) => {
          const OptionIcon = softwareIcons[item.id as keyof typeof softwareIcons] ?? CircuitBoard
          return <button className="software-picker__option" type="button" role="option" aria-selected={item.id === selectedId} key={item.id} onClick={() => { onSelect(item.id); setOpen(false) }}>
            <OptionIcon size={16} /><span>{item.name}</span>{item.id === selectedId && <Check size={15} />}
          </button>
        })}
      </div>}
    </div>
  )
}
