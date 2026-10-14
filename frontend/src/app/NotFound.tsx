import { Compass } from 'lucide-react';
import { Link } from 'react-router-dom';

import { Button } from '../components/ui';

export default function NotFound(): JSX.Element {
  return (
    <div className="flex h-full min-h-[60vh] flex-col items-center justify-center text-center">
      <Compass aria-hidden className="h-6 w-6 text-fg-faint" strokeWidth={1.5} />
      <h1 className="mt-4 text-lg font-semibold text-fg">404 — Page not found</h1>
      <p className="mt-1 max-w-xs text-[13px] text-fg-subtle">
        That route doesn&apos;t exist. It may have moved, or the link was mistyped.
      </p>
      <Link to="/dashboard" className="mt-5">
        <Button variant="secondary">Back to dashboard</Button>
      </Link>
    </div>
  );
}
