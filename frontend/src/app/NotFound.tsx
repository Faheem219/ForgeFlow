import { Link } from 'react-router-dom';

import { Button, EmptyState } from '../components/ui';

export default function NotFound(): JSX.Element {
  return (
    <EmptyState
      title="404 — Page not found"
      description="That route doesn't exist."
      action={
        <Link to="/">
          <Button>Back to dashboard</Button>
        </Link>
      }
    />
  );
}
