import { cn } from '../../lib/cn'
import { useUsage } from './api'
import { formatResetTime } from './notices'

// Inline `style` is forbidden by the CSP, so the bar width is one of eleven fixed classes.
const WIDTH = [
  'w-0',
  'w-[10%]',
  'w-[20%]',
  'w-[30%]',
  'w-[40%]',
  'w-[50%]',
  'w-[60%]',
  'w-[70%]',
  'w-[80%]',
  'w-[90%]',
  'w-full',
] as const

const usd = (n: number) => `$${n.toFixed(2)}`

/** Today's spend against the daily budget, in the sidebar (FR-053). Quiet if it cannot load. */
export function UsageMeter() {
  const { data } = useUsage()
  if (!data) return null

  const fraction = data.budget_usd > 0 ? Math.min(data.spend_usd / data.budget_usd, 1) : 1
  const full = data.spend_usd >= data.budget_usd
  const near = fraction >= 0.8

  return (
    <section aria-label="Usage today" className="mt-3 px-2">
      <p className="flex justify-between text-caption text-fg-muted">
        <span>Today</span>
        <span>
          {usd(data.spend_usd)} of {usd(data.budget_usd)}
        </span>
      </p>
      <div
        role="progressbar"
        aria-label="Daily spend"
        aria-valuemin={0}
        aria-valuemax={data.budget_usd}
        aria-valuenow={Math.min(data.spend_usd, data.budget_usd)}
        aria-valuetext={`${usd(data.spend_usd)} of ${usd(data.budget_usd)}`}
        className="mt-1 h-1.5 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800"
      >
        <div
          className={cn(
            'h-full rounded-full',
            WIDTH[Math.round(fraction * 10)],
            full ? 'bg-error-fg' : near ? 'bg-secondary' : 'bg-primary',
          )}
        />
      </div>
      {(full || data.global_cap_reached) && (
        <p role="status" className="mt-1 text-caption text-error-fg">
          {data.global_cap_reached
            ? `Nexus is at its daily limit for everyone. Resets ${formatResetTime(data.resets_at)}.`
            : `Daily limit reached. Resets ${formatResetTime(data.resets_at)}.`}
        </p>
      )}
    </section>
  )
}
