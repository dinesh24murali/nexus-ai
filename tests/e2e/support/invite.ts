import { execFileSync } from 'node:child_process'
import path from 'node:path'

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..')

/** Mint a real invite with the admin CLI inside the running api container (what an operator does). */
export function createInvite(note = 'e2e'): string {
  const out = execFileSync(
    'docker',
    ['compose', 'exec', '-T', 'api', 'python', '-m', 'app.cli', 'invite', 'create', '--note', note],
    { cwd: REPO_ROOT, encoding: 'utf8' },
  )
  const match = out.match(/nx_[0-9a-f]{32}/)
  if (!match) throw new Error(`could not find an invite code in CLI output:\n${out}`)
  return match[0]
}

let counter = 0
/** A unique, valid email per call so tests never collide with data from earlier runs. */
export function uniqueEmail(prefix = 'e2e'): string {
  counter += 1
  return `${prefix}-${Date.now().toString(36)}-${counter}@example.com`
}

export const PASSWORD = 'correct-horse-battery'
