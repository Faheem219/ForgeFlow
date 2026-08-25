import { render } from '@testing-library/react';
import type { ComponentProps } from 'react';
import type { PanelProps } from 'react-resizable-panels';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Pane } from './SplitPane';

/**
 * `Pane` exists to express sizes the way the workspace thinks about them — "the rail is a fifth of
 * the width" — and hand the library something it reads the same way.
 *
 * That translation is load-bearing, not cosmetic. `react-resizable-panels` treats a bare number as
 * **pixels**; only strings without a unit are percentages. Passing `minSize={12}` straight through
 * therefore pins a pane to 12 *pixels*, which is how the stage rail and the assistant once ended up
 * as slivers of sideways text that no amount of dragging could widen. Capture the props the library
 * actually receives so a regression here fails loudly instead of shipping as a broken layout.
 */
const panelProps = vi.fn((props: PanelProps) => props);

vi.mock('react-resizable-panels', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-resizable-panels')>();
  return {
    ...actual,
    Panel: (props: PanelProps) => {
      panelProps(props);
      return <div data-panel-stub>{props.children}</div>;
    },
  };
});

function renderPane(props: Partial<ComponentProps<typeof Pane>>) {
  render(
    <Pane id="rail" {...props}>
      rail
    </Pane>,
  );
  return panelProps.mock.calls[0][0];
}

describe('Pane sizing', () => {
  beforeEach(() => panelProps.mockClear());

  it('sends numeric sizes as percentages, so a pane is never sized in pixels', () => {
    const props = renderPane({ defaultSize: 18, minSize: 12, maxSize: 34 });

    expect(props.defaultSize).toBe('18%');
    expect(props.minSize).toBe('12%');
    expect(props.maxSize).toBe('34%');
  });

  it('leaves a size that already carries a unit alone', () => {
    const props = renderPane({ minSize: '240px', maxSize: '20rem' });

    expect(props.minSize).toBe('240px');
    expect(props.maxSize).toBe('20rem');
  });

  it('omits sizes that were not given, so the library applies its own defaults', () => {
    const props = renderPane({});

    expect(props.defaultSize).toBeUndefined();
    expect(props.minSize).toBeUndefined();
    expect(props.maxSize).toBeUndefined();
  });

  it('converts a collapsed size too — a collapsible pane collapses to a share, not to 0px', () => {
    const props = renderPane({ collapsible: true, collapsedSize: 4 });

    expect(props.collapsible).toBe(true);
    expect(props.collapsedSize).toBe('4%');
  });
});
