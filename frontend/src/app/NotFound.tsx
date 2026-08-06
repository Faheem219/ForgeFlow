import { Compass } from 'lucide-react';
import { Link } from 'react-router-dom';

import { Button, EmptyState } from '../components/ui';

export default function NotFound(): JSX.Element {
  return (
    <div className="flex h-full min-h-[60vh] items-center justify-center">
      <EmptyState
        icon={<Compass aria-hidden className="h-6 w-6" strokeWidth={1.5} />}
        title="404 — Page not found"
        description="That route doesn't exist."
        action={
          <Link to="/dashboard">
            <Button>Back to dashboard</Button>
          </Link>
        }
      />
    </div>
  );
}
