import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DesignPreview } from './DesignPreview';

const FULL_DOC = `<!doctype html><html><head><script src="https://cdn.tailwindcss.com"></script><style>body{margin:0}</style></head><body><main class="p-4">Focus</main></body></html>`;

describe('DesignPreview', () => {
  it('shows an empty state when there is nothing to render', () => {
    render(<DesignPreview html="" css="" />);
    expect(screen.getByText('Nothing to preview')).toBeInTheDocument();
    expect(screen.queryByTestId('design-preview-frame')).not.toBeInTheDocument();
  });

  it('scaffolds a fragment into a document', () => {
    render(<DesignPreview html="<h1>hi</h1>" css="h1{color:red}" />);
    const srcDoc = screen.getByTestId('design-preview-frame').getAttribute('srcdoc') ?? '';

    expect(srcDoc).toContain('<!doctype html>');
    expect(srcDoc).toContain('<h1>hi</h1>');
    expect(srcDoc).toContain('h1{color:red}');
  });

  it('renders a complete document verbatim rather than nesting it in another body', () => {
    // Stitch returns a full document; wrapping it would nest <html>/<head> and drop its styling.
    render(<DesignPreview html={FULL_DOC} css="" />);
    const srcDoc = screen.getByTestId('design-preview-frame').getAttribute('srcdoc') ?? '';

    expect(srcDoc).toBe(FULL_DOC);
    expect(srcDoc.match(/<html/gi)).toHaveLength(1);
    expect(srcDoc).toContain('cdn.tailwindcss.com'); // the stylesheet loader survives intact
  });

  it('injects extra css into a complete document without breaking it', () => {
    render(<DesignPreview html={FULL_DOC} css="body{background:pink}" />);
    const srcDoc = screen.getByTestId('design-preview-frame').getAttribute('srcdoc') ?? '';

    expect(srcDoc.match(/<html/gi)).toHaveLength(1);
    // Appended last, inside <head>, so it wins over the document's own rules.
    expect(srcDoc).toContain('<style>body{background:pink}</style></head>');
  });

  it('tabs between screens when a design has several', () => {
    render(
      <DesignPreview
        html="<p>primary</p>"
        css=""
        screens={[
          { id: 's1', title: 'Todo (light)', html: '<p>light</p>' },
          { id: 's2', title: 'Todo (dark)', html: '<p>dark</p>' },
        ]}
      />,
    );

    // The primary screen shows first.
    const frame = () => screen.getByTestId('design-preview-frame').getAttribute('srcdoc') ?? '';
    expect(frame()).toContain('<p>light</p>');
    expect(screen.getByText('Todo (dark)')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('screen-tab-s2'));
    expect(frame()).toContain('<p>dark</p>');
  });

  it('shows no tab bar for a single-screen design', () => {
    render(
      <DesignPreview html="<p>only</p>" css="" screens={[{ id: 's1', title: 'x', html: '' }]} />,
    );
    expect(screen.queryByTestId('design-screens')).not.toBeInTheDocument();
    expect(screen.getByTestId('design-preview-frame').getAttribute('srcdoc')).toContain('only');
  });

  it('sandboxes the frame with scripts but never same-origin', () => {
    render(<DesignPreview html={FULL_DOC} css="" />);
    const sandbox = screen.getByTestId('design-preview-frame').getAttribute('sandbox') ?? '';

    // Scripts are needed: real design exports (Stitch's Tailwind CDN) style themselves at runtime.
    expect(sandbox).toContain('allow-scripts');
    // The load-bearing guarantee: an opaque origin. `allow-scripts allow-same-origin` together
    // would let untrusted markup reach the platform DOM, cookies and API.
    expect(sandbox).not.toContain('allow-same-origin');
    // Nor may it submit forms, open windows, or navigate the top frame.
    expect(sandbox).not.toContain('allow-forms');
    expect(sandbox).not.toContain('allow-popups');
    expect(sandbox).not.toContain('allow-top-navigation');
  });
});
