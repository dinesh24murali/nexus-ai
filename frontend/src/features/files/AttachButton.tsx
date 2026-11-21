import { Paperclip } from 'lucide-react'
import { useRef, type ChangeEvent } from 'react'
import { Button } from '../../components/ui/Button'
import { ACCEPT } from './messages'

/** Opens the file picker; the chosen files go to `onPick` (the same input serves every caller). */
export function FilePicker({
  onPick,
  children,
  variant = 'ghost',
}: {
  onPick: (files: File[]) => void
  children?: string
  variant?: 'ghost' | 'secondary'
}) {
  const input = useRef<HTMLInputElement>(null)

  function onChange(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? [])
    event.target.value = '' // the same file can be chosen again
    if (files.length > 0) onPick(files)
  }

  return (
    <>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        onChange={onChange}
        hidden
        aria-label="Choose files to upload"
      />
      <Button
        type="button"
        variant={variant}
        size="sm"
        aria-label={children ? undefined : 'Attach files'}
        onClick={() => input.current?.click()}
        className={children ? undefined : 'size-10 shrink-0 px-0'}
      >
        <Paperclip className="size-4" aria-hidden />
        {children}
      </Button>
    </>
  )
}
