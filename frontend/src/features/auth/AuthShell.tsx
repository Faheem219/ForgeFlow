import { useRef } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { AmbientBackground } from '../../components/AmbientBackground';
import { BrandMark } from '../../components/BrandMark';
import { EASE_OUT, gsap, motionOK, useGSAP } from '../../lib/gsap';
import { STAGE_ICONS, STAGE_ORDER } from '../workspace/stageMeta';

/**
 * The signed-out frame. It borrows the landing page's atmosphere — the same grid, the same orbs —
 * so arriving here from `/` feels like stepping closer, not into a different product. The card
 * itself holds only the task; the brand mark above it doubles as the way back out.
 *
 * Entrance is one GSAP timeline (mark → title → card → fields), gated on `motionOK()` so reduced
 * motion and tests see the finished frame immediately.
 */
export function AuthShell({
  title,
  children,
  footer,
}: {
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}): JSX.Element {
  const scope = useRef<HTMLElement | null>(null);

  useGSAP(
    () => {
      if (!motionOK()) return;
      const tl = gsap.timeline({ defaults: { ease: EASE_OUT } });
      tl.from('[data-auth-brand]', { y: -14, opacity: 0, duration: 0.5 })
        .from('[data-auth-card]', { y: 22, opacity: 0, scale: 0.985, duration: 0.55 }, '-=0.3')
        .from(
          '[data-auth-fields] > *',
          { y: 10, opacity: 0, duration: 0.4, stagger: 0.06, clearProps: 'all' },
          '-=0.35',
        )
        .from('[data-auth-footer]', { opacity: 0, duration: 0.4 }, '-=0.2')
        .from(
          '[data-auth-stage]',
          { y: 8, opacity: 0, duration: 0.35, stagger: 0.05, clearProps: 'all' },
          '-=0.35',
        );
    },
    { scope },
  );

  return (
    <main
      ref={scope}
      className="relative flex min-h-screen items-center justify-center overflow-hidden bg-surface-canvas p-6"
    >
      <AmbientBackground intensity="bold" />

      <div className="relative w-full max-w-sm">
        <div data-auth-brand className="mb-5 flex justify-center">
          <Link to="/" className="flex items-center gap-2.5 rounded-lg" aria-label="ForgeFlow home">
            <BrandMark size={30} />
            <span className="text-lg font-semibold tracking-tight text-fg">ForgeFlow</span>
          </Link>
        </div>

        {/* A solid card — frosted glass over a glow is the generated-UI default, and this page
            has nothing behind it worth blurring. One brand hairline marks the top edge. */}
        <div
          data-auth-card
          className="relative overflow-hidden rounded-2xl border border-edge bg-surface p-8 shadow-lg"
        >
          <span aria-hidden className="absolute inset-x-0 top-0 h-px bg-brand/50" />
          {/* The card's own heading carries real weight — at `text-sm text-fg-muted` it read as a
              caption, leaving the form with no owner. */}
          <h1 className="mb-6 text-center text-base font-semibold tracking-tight text-fg">
            {title}
          </h1>
          <div data-auth-fields>{children}</div>
        </div>

        {footer ? (
          <p data-auth-footer className="mt-6 text-center text-sm text-fg-muted">
            {footer}
          </p>
        ) : null}

        {/* The pipeline, quoted in miniature — a quiet reminder of what's on the other side. */}
        <div className="mt-8 flex items-center justify-center gap-3" aria-hidden>
          {STAGE_ORDER.map((stage, i) => {
            const Icon = STAGE_ICONS[stage];
            return (
              <span key={stage} data-auth-stage className="flex items-center gap-3">
                {i > 0 ? <span className="h-px w-3 bg-edge" /> : null}
                <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-edge bg-surface/80 text-fg-faint">
                  <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
                </span>
              </span>
            );
          })}
        </div>
      </div>
    </main>
  );
}
