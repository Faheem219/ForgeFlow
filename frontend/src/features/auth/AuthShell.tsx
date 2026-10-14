import type { CSSProperties, ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { AmbientBackground } from '../../components/AmbientBackground';
import { BrandMark } from '../../components/BrandMark';

/** Staggered entrance: each block rises in a beat after the one above it. */
const step = (i: number): CSSProperties => ({ animationDelay: `${i * 60}ms` });

/**
 * The signed-out frame. It borrows the landing page's quiet grid so arriving here from `/` feels
 * like stepping closer, not into a different product. The card holds only the task; the brand mark
 * above it doubles as the way back out.
 *
 * The entrance is plain CSS (`motion-safe:` keyframes with staggered delays) — this route used to
 * load GSAP and three plugins to fade a card in.
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
  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-surface-canvas p-6">
      <AmbientBackground intensity="bold" />

      <div className="relative w-full max-w-[22rem]">
        <div className="mb-8 flex justify-center motion-safe:animate-ff-rise" style={step(0)}>
          <Link
            to="/"
            className="flex items-center gap-2.5 rounded-lg px-1"
            aria-label="ForgeFlow home"
          >
            <BrandMark size={26} />
            <span className="text-[15px] font-semibold tracking-tight text-fg">ForgeFlow</span>
          </Link>
        </div>

        {/* A solid card — frosted glass over a glow is the generated-UI default, and this page has
            nothing behind it worth blurring. */}
        <div
          className="rounded-xl border border-edge bg-surface p-7 shadow-raise motion-safe:animate-ff-rise"
          style={step(1)}
        >
          <h1 className="mb-5 text-lg font-semibold tracking-tight text-fg">{title}</h1>
          <div>{children}</div>
        </div>

        {footer ? (
          <p
            className="mt-5 text-center text-[13px] text-fg-subtle motion-safe:animate-ff-rise"
            style={step(2)}
          >
            {footer}
          </p>
        ) : null}
      </div>
    </main>
  );
}
