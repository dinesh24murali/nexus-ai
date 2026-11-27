import { Monitor, Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '../lib/cn'
import {
  readPreference,
  savePreference,
  watchSystemTheme,
  type ThemePreference,
} from '../lib/theme'

const options = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
] as const

export function ThemeToggle({ className }: { className?: string }) {
  const [pref, setPref] = useState<ThemePreference>(() => readPreference())

  useEffect(() => watchSystemTheme(() => pref), [pref])

  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className={cn('inline-flex rounded-md border border-border bg-surface p-0.5', className)}
    >
      {options.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={pref === value}
          aria-label={label}
          title={label}
          onClick={() => {
            setPref(value)
            savePreference(value)
          }}
          className={cn(
            'flex size-7 items-center justify-center rounded-sm text-fg-muted transition-colors',
            pref === value ? 'bg-neutral-200/70 text-fg dark:bg-neutral-800' : 'hover:text-fg',
          )}
        >
          <Icon className="size-4" aria-hidden />
        </button>
      ))}
    </div>
  )
}
