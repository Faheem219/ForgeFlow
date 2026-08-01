import { useState } from 'react';

import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Input,
  Kbd,
  Modal,
  Panel,
  ProgressBar,
  ResizablePanel,
  Segmented,
  Skeleton,
  SkeletonRows,
  Spinner,
  Stat,
  Tabs,
  Textarea,
  Tooltip,
} from '../../components/ui';
import { ThemeToggle } from '../../app/layout/ThemeToggle';
import { PanelKey } from '../../lib/stores/uiStore';
import { toast } from '../../lib/stores/toastStore';

/**
 * Dev-only showcase of the UI kit (route: /_kit).
 *
 * Doubles as the manual theme check: flip the toggle at the top and every primitive below should
 * stay legible without a single component knowing which theme it is in.
 */
export default function UiKit(): JSX.Element {
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [density, setDensity] = useState<'compact' | 'cosy'>('compact');

  return (
    <main className="mx-auto max-w-4xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-primary">UI Kit</h1>
          <p className="mt-0.5 text-sm text-muted">
            Every primitive, in the current theme. Flip the toggle to check both.
          </p>
        </div>
        <ThemeToggle />
      </header>

      <Panel title="Buttons" description="Active state is a scale-down, not a colour swap">
        <div className="flex flex-wrap items-center gap-2">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="subtle">Subtle</Button>
          <Button variant="danger">Danger</Button>
          <Button size="sm">Small</Button>
          <Button loading>Loading</Button>
          <Button disabled>Disabled</Button>
        </div>
      </Panel>

      <Panel title="Badges" description="Each tone carries its own fg/bg/border pair">
        <div className="flex flex-wrap gap-2">
          <Badge>neutral</Badge>
          <Badge tone="brand">brand</Badge>
          <Badge tone="success" dot>
            complete
          </Badge>
          <Badge tone="warning" dot>
            needs you
          </Badge>
          <Badge tone="danger">failed</Badge>
          <Badge tone="info" dot pulse>
            streaming
          </Badge>
        </div>
      </Panel>

      <Panel title="Fields">
        <div className="grid gap-4 sm:grid-cols-2">
          <Input label="Email" type="email" placeholder="you@example.com" />
          <Input label="With error" defaultValue="nope" error="That name is already taken." />
          <Input label="With hint" placeholder="my-app" hint="Lowercase, no spaces." />
          <Textarea label="Change request" rows={3} placeholder="e.g. add a delete button" />
        </div>
      </Panel>

      <Panel title="Controls">
        <div className="flex flex-wrap items-center gap-4">
          <Segmented
            ariaLabel="Density"
            value={density}
            onChange={setDensity}
            options={[
              { value: 'compact', label: 'Compact' },
              { value: 'cosy', label: 'Cosy' },
            ]}
          />
          <Tooltip label="This is a tooltip">
            <Button variant="secondary" size="sm">
              Hover me
            </Button>
          </Tooltip>
          <span className="flex items-center gap-1.5 text-xs text-muted">
            Search <Kbd>⌘</Kbd>
            <Kbd>K</Kbd>
          </span>
        </div>
      </Panel>

      <Panel title="Metrics" description="Tabular numerals so live values don't reflow">
        <dl className="flex flex-wrap gap-6">
          <Stat label="Iterations" value="4" />
          <Stat label="Failing" value="0" tone="success" hint="down from 7" />
          <Stat label="Regressions" value="1" tone="warning" />
          <Stat label="Cost" value="₹12.40" />
        </dl>
      </Panel>

      <Panel title="Tabs">
        <Tabs
          items={[
            {
              id: 'a',
              label: 'Overview',
              content: <p className="text-sm text-muted">Overview panel.</p>,
            },
            {
              id: 'b',
              label: 'Failures',
              badge: <Badge tone="danger">3</Badge>,
              content: <p className="text-sm text-muted">Failures panel.</p>,
            },
          ]}
        />
      </Panel>

      <Panel title="Loading">
        <div className="space-y-4">
          <div className="flex items-center gap-4">
            <Spinner size="sm" />
            <Spinner />
            <Spinner size="lg" />
          </div>
          <ProgressBar label="Generating" />
          <div className="flex gap-3">
            <Skeleton className="h-8 w-8 rounded-full" />
            <div className="flex-1">
              <SkeletonRows rows={2} />
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="Resizable" description="Drag the seam, or focus it and use arrows" flush>
        <div className="flex h-32">
          <ResizablePanel
            panelKey={PanelKey.dataCollections}
            label="Resize demo pane"
            min={120}
            max={360}
            className="bg-inset"
          >
            <p className="p-3 text-xs text-muted">Fixed pane</p>
          </ResizablePanel>
          <div className="flex-1 p-3 text-xs text-muted">Flexible pane</div>
        </div>
      </Panel>

      <Panel title="Overlays">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => setModalOpen(true)}>
            Open modal
          </Button>
          <Button variant="danger" onClick={() => setConfirmOpen(true)}>
            Destructive confirm
          </Button>
          {(['info', 'success', 'warning', 'error'] as const).map((variant) => (
            <Button
              key={variant}
              variant="secondary"
              size="sm"
              onClick={() =>
                toast({
                  title: `A ${variant} toast`,
                  description: 'With a description that explains what happened.',
                  variant,
                })
              }
            >
              {variant}
            </Button>
          ))}
        </div>
      </Panel>

      <EmptyState
        title="Nothing here yet"
        description="EmptyState is used for empty lists and placeholders."
        action={<Button size="sm">Do something</Button>}
      />

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title="Confirm action"
        description="Modals trap focus and restore it on close."
        footer={
          <>
            <Button variant="ghost" onClick={() => setModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => setModalOpen(false)}>Confirm</Button>
          </>
        }
      >
        Press Escape, click the backdrop, or Tab around — focus stays inside.
      </Modal>

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => setConfirmOpen(false)}
        title="Delete something"
        confirmLabel="Delete it"
        confirmPhrase="my-project"
        description="Typing the exact name is the friction that makes this a decision, not a reflex."
      />
    </main>
  );
}
