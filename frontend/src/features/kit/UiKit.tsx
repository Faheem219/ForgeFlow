import { useState } from 'react';

import { Badge, Button, EmptyState, Input, Modal, Panel, Spinner, Tabs } from '../../components/ui';
import { toast } from '../../lib/stores/toastStore';

/** Dev-only showcase of every UI-kit component (route: /_kit). */
export default function UiKit(): JSX.Element {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <main className="mx-auto max-w-3xl space-y-8 p-8">
      <h1 className="text-2xl font-semibold text-slate-100">UI Kit</h1>

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
        </div>
      </Panel>

      <Panel title="Tabs">
        <Tabs
          items={[
            {
              id: 'a',
              label: 'Overview',
              content: <p className="text-sm text-slate-400">Overview panel.</p>,
            },
            {
              id: 'b',
              label: 'Details',
              content: <p className="text-sm text-slate-400">Details panel.</p>,
            },
          ]}
        />
      </Panel>

      <Panel title="Feedback">
        <div className="flex flex-wrap items-center gap-4">
          <Spinner />
          <Button variant="secondary" onClick={() => setModalOpen(true)}>
            Open modal
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
    </main>
  );
}
