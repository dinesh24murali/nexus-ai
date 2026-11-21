import { Alert } from '../../components/ui/Alert'
import { Button } from '../../components/ui/Button'
import { describeNotice, type Notice } from './notices'

type Props = {
  notice: Notice
  onReload: () => void
  onDismiss: () => void
}

export function RunNotice({ notice, onReload, onDismiss }: Props) {
  const view = describeNotice(notice)
  return (
    <Alert variant={notice.kind === 'restarting' || notice.kind === 'busy' ? 'info' : 'error'}>
      <p>{view.message}</p>
      <div className="mt-2 flex gap-2">
        {view.action === 'reload' && (
          <Button size="sm" variant="secondary" onClick={onReload}>
            Reload
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </Alert>
  )
}
