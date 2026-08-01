import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { PANEL_DEFAULTS, PanelKey, useUiStore } from '../../lib/stores/uiStore';
import { ResizablePanel } from './Resizable';

/**
 * jsdom has no layout and no real PointerEvent, so these assert the parts that are actually ours:
 * the size the pane is given, what gets committed to the store and when, and the keyboard contract.
 * Pixel-accurate dragging is the browser's job, not something a unit test can meaningfully claim.
 */

const KEY = PanelKey.ideTree;

/**
 * jsdom has no `PointerEvent`, so a `MouseEvent` stands in. `clientX`/`clientY`/`button` come from
 * the constructor (they are read-only getters); only `pointerId` has to be grafted on, since React
 * reads it for the capture calls.
 */
function pointer(type: string, clientX: number): Event {
  const event = new MouseEvent(type, { bubbles: true, clientX, clientY: 0, button: 0 });
  Object.defineProperty(event, 'pointerId', { value: 1 });
  return event;
}

beforeEach(() => {
  localStorage.clear();
  useUiStore.setState({ panelSizes: {} });
  // jsdom does not implement pointer capture.
  Element.prototype.setPointerCapture = function setPointerCapture(): void {};
  Element.prototype.releasePointerCapture = function releasePointerCapture(): void {};
  Element.prototype.hasPointerCapture = function hasPointerCapture(): boolean {
    return true;
  };
});

function renderPanel(): HTMLElement {
  render(
    <ResizablePanel panelKey={KEY} label="Resize file tree" min={160} max={480}>
      <div>tree</div>
    </ResizablePanel>,
  );
  return screen.getByTestId(`splitter-${KEY}`);
}

describe('ResizablePanel', () => {
  it('starts at the configured default size', () => {
    const handle = renderPanel();
    expect(handle.getAttribute('aria-valuenow')).toBe(String(PANEL_DEFAULTS[KEY]));
  });

  it('exposes a separator with a full ARIA range', () => {
    const handle = renderPanel();
    expect(handle).toHaveAttribute('role', 'separator');
    expect(handle).toHaveAttribute('aria-label', 'Resize file tree');
    expect(handle).toHaveAttribute('aria-valuemin', '160');
    expect(handle).toHaveAttribute('aria-valuemax', '480');
  });

  it('commits the size once on release, not on every move', () => {
    const handle = renderPanel();
    const start = PANEL_DEFAULTS[KEY];

    fireEvent(handle, pointer('pointerdown', 0));
    fireEvent(handle, pointer('pointermove', 40));
    // Mid-drag the store is untouched — this is what keeps a Monaco-bearing pane from
    // re-rendering 60x/second while the handle moves.
    expect(useUiStore.getState().panelSizes[KEY]).toBeUndefined();

    fireEvent(handle, pointer('pointerup', 40));
    expect(useUiStore.getState().panelSizes[KEY]).toBe(start + 40);
  });

  it('clamps to min and max', () => {
    const handle = renderPanel();

    fireEvent(handle, pointer('pointerdown', 0));
    fireEvent(handle, pointer('pointermove', -9999));
    fireEvent(handle, pointer('pointerup', -9999));
    expect(useUiStore.getState().panelSizes[KEY]).toBe(160);

    fireEvent(handle, pointer('pointerdown', 0));
    fireEvent(handle, pointer('pointermove', 9999));
    fireEvent(handle, pointer('pointerup', 9999));
    expect(useUiStore.getState().panelSizes[KEY]).toBe(480);
  });

  it('ignores moves that arrive without a drag in progress', () => {
    const handle = renderPanel();
    fireEvent(handle, pointer('pointermove', 200));
    expect(useUiStore.getState().panelSizes[KEY]).toBeUndefined();
  });

  it('resizes with the keyboard, in larger steps with Shift', () => {
    const handle = renderPanel();
    const start = PANEL_DEFAULTS[KEY];

    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(useUiStore.getState().panelSizes[KEY]).toBe(start + 16);

    fireEvent.keyDown(handle, { key: 'ArrowRight', shiftKey: true });
    expect(useUiStore.getState().panelSizes[KEY]).toBe(start + 80);

    fireEvent.keyDown(handle, { key: 'Home' });
    expect(useUiStore.getState().panelSizes[KEY]).toBe(160);

    fireEvent.keyDown(handle, { key: 'End' });
    expect(useUiStore.getState().panelSizes[KEY]).toBe(480);
  });

  it('inverts the drag direction for a panel on the end side', () => {
    render(
      <ResizablePanel
        panelKey={PanelKey.workspaceChat}
        side="end"
        label="Resize chat"
        min={100}
        max={900}
      >
        <div>chat</div>
      </ResizablePanel>,
    );
    const handle = screen.getByTestId(`splitter-${PanelKey.workspaceChat}`);
    const start = PANEL_DEFAULTS[PanelKey.workspaceChat];

    // Pointer moves right (+60); a right-hand panel should SHRINK.
    fireEvent(handle, pointer('pointerdown', 0));
    fireEvent(handle, pointer('pointermove', 60));
    fireEvent(handle, pointer('pointerup', 60));
    expect(useUiStore.getState().panelSizes[PanelKey.workspaceChat]).toBe(start - 60);
  });

  it('restores the default on double-click', () => {
    const handle = renderPanel();
    // Store writes drive a re-render, so they belong inside act() like any other update.
    act(() => useUiStore.getState().setPanelSize(KEY, 400));

    fireEvent.doubleClick(handle);
    expect(useUiStore.getState().panelSizes[KEY]).toBe(PANEL_DEFAULTS[KEY]);
  });

  it('clears the global drag class when unmounted mid-drag', () => {
    const { unmount } = render(
      <ResizablePanel panelKey={KEY} label="Resize file tree">
        <div>tree</div>
      </ResizablePanel>,
    );
    const handle = screen.getByTestId(`splitter-${KEY}`);

    fireEvent(handle, pointer('pointerdown', 0));
    expect(document.body.classList.contains('dragging-none')).toBe(true);

    unmount();
    expect(document.body.classList.contains('dragging-none')).toBe(false);
  });
});
