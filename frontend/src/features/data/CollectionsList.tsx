import { Badge, EmptyState, Spinner } from '../../components/ui';
import type { CollectionDto } from '../../lib/types';

/**
 * The collections in this project's database. Counts are MongoDB's fast estimates, which is the
 * right trade for a sidebar — an exact count on every render would scan every collection.
 */
export function CollectionsList({
  collections,
  selected,
  loading = false,
  onSelect,
}: {
  collections: CollectionDto[];
  selected: string | null;
  loading?: boolean;
  onSelect: (name: string) => void;
}): JSX.Element {
  if (loading) {
    return (
      <div className="flex justify-center py-6" data-testid="collections-loading">
        <Spinner />
      </div>
    );
  }

  if (collections.length === 0) {
    return (
      <EmptyState
        title="No collections yet"
        description="Your app's collections appear here once it writes its first document."
      />
    );
  }

  return (
    <ul className="space-y-1" data-testid="collections-list">
      {collections.map((collection) => {
        const active = collection.name === selected;
        return (
          <li key={collection.name}>
            <button
              type="button"
              data-testid={`collection-${collection.name}`}
              aria-current={active ? 'true' : undefined}
              onClick={() => onSelect(collection.name)}
              className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition ${
                active ? 'bg-brand/15 text-brand-text' : 'text-fg-muted hover:bg-surface-raised'
              }`}
            >
              <span className="truncate font-mono text-xs">{collection.name}</span>
              <Badge tone={active ? 'brand' : 'neutral'}>{collection.count}</Badge>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default CollectionsList;
