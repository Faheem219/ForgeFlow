import { useState } from 'react';

import { ThemeToggle } from '../../app/layout/ThemeToggle';
import { AgentWorking } from '../../components/AgentWorking';
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Input,
  Kbd,
  Modal,
  Panel,
  Segmented,
  Skeleton,
  Spinner,
  Stat,
  Tabs,
  Tooltip,
} from '../../components/ui';
import { toast } from '../../lib/stores/toastStore';

/** Dev-only showcase of every UI-kit component (route: /_kit). */
export default function UiKit(): JSX.Element {
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [segment, setSegment] = useState<'code' | 'preview'>('code');

  return (
    <main className="mx-auto max-w-3xl space-y-8 p-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-fg">UI Kit</h1>
        <ThemeToggle />
      </div>

      <Panel title="Buttons">
        <div className="flex flex-wrap items-center gap-3">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button size="sm">Small</Button>
          <Button loading>Loading</Button>
          <Button disabled>Disabled</Button>
        </div>
      </Panel>

      <Panel title="Badges">
        <div className="flex flex-wrap gap-2">
          <Badge>neutral</Badge>
          <Badge tone="brand">brand</Badge>
          <Badge tone="success">success</Badge>
          <Badge tone="warning">warning</Badge>
          <Badge tone="danger">danger</Badge>
        </div>
      </Panel>

      <Panel title="Input">
        <div className="max-w-sm space-y-3">
          <Input label="Email" type="email" placeholder="you@example.com" />
          <Input placeholder="No label" />
          <Input label="With hint" hint="Shown under the field." placeholder="…" />
        </div>
      </Panel>

      <Panel title="Tabs">
        <Tabs
          items={[
            {
              id: 'a',
              label: 'Overview',
              content: <p className="text-sm text-fg-subtle">Overview panel.</p>,
            },
            {
              id: 'b',
              label: 'Details',
              content: <p className="text-sm text-fg-subtle">Details panel.</p>,
            },
          ]}
        />
      </Panel>

      <Panel title="Small pieces">
        <div className="flex flex-wrap items-center gap-4">
          <span className="flex items-center gap-1 text-sm text-fg-muted">
            Command palette <Kbd>Ctrl</Kbd>
            <Kbd>K</Kbd>
          </span>
          <Tooltip label="A short explanation">
            <Button size="sm" variant="ghost">
              Hover me
            </Button>
          </Tooltip>
          <Segmented
            ariaLabel="View"
            value={segment}
            onChange={setSegment}
            options={[
              { value: 'code', label: 'Code' },
              { value: 'preview', label: 'Preview' },
            ]}
          />
        </div>
        <div className="mt-4 grid max-w-md grid-cols-3 gap-3">
          <Stat label="Tests" value="128" tone="success" detail="all passing" />
          <Stat label="Cost" value="₹41.20" />
          <Stat label="Repairs" value="2" tone="danger" />
        </div>
        <div className="mt-4 max-w-sm space-y-2">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      </Panel>

      {/* The long-wait indicator lives here too: it only appears mid-run inside the build and
          design panels, which makes it almost impossible to look at while working on it. */}
      <Panel title="Agent working (long waits)">
        <div className="flex justify-center py-4">
          <AgentWorking label="Generating the app" />
        </div>
      </Panel>

      <Panel title="Feedback">
        <div className="flex flex-wrap items-center gap-4">
          <Spinner />
          <Button variant="secondary" onClick={() => setModalOpen(true)}>
            Open modal
          </Button>
          <Button variant="secondary" onClick={() => setConfirmOpen(true)}>
            Open confirm
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              toast({ title: 'Saved', description: 'Your changes were saved.', variant: 'success' })
            }
          >
            Raise toast
          </Button>
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
        footer={
          <>
            <Button variant="ghost" onClick={() => setModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => setModalOpen(false)}>Confirm</Button>
          </>
        }
      >
        This is a modal dialog. Press Escape or click the backdrop to close.
      </Modal>

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => setConfirmOpen(false)}
        title="Delete example"
        confirmPhrase="example"
        confirmLabel="Delete forever"
        description="Typed-name confirmation for irreversible actions."
      />
    </main>
  );
}
