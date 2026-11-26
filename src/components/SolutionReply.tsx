import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import type { ReactNode } from 'react'
import 'katex/dist/katex.min.css'

export default function SolutionReply({ text, renderImage }: { text: string; renderImage?: (path: string) => ReactNode }) {
  const displayMath = text.replace(/\\\[([\s\S]*?)\\\]/g, (_match, formula: string) => `$$${formula}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, formula: string) => `$${formula}$`)
    .replace(/\$\$([\s\S]*?)\$\$/g, (_match, formula: string) => `\n$$\n${formula.trim()}\n$$\n`)
    .split('\n').map((line) => {
      if (!line.startsWith('|')) return line
      let insideMath = false
      return line.replace(/\$|\|/g, (token) => {
        if (token === '$') insideMath = !insideMath
        return token === '|' && insideMath ? '\\|' : token
      })
    }).join('\n')
  return <div className="conversation__markdown"><ReactMarkdown remarkPlugins={[remarkMath, remarkGfm]} rehypePlugins={[rehypeKatex]} components={{ img: ({ src }) => typeof src === 'string' ? renderImage?.(src) : null }}>{displayMath}</ReactMarkdown></div>
}
