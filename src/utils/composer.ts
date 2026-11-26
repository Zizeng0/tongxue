import type { KeyboardEvent } from 'react'

export function submitOnEnter(event: KeyboardEvent<HTMLTextAreaElement>, submitting: boolean) {
  if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
  event.preventDefault()
  if (!submitting) event.currentTarget.form?.requestSubmit()
}
