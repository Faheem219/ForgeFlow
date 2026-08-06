import { useRef } from 'react';

import { gsap, motionOK, useGSAP } from '../lib/gsap';
import { AmbientCanvas } from './AmbientCanvas';

/**
 * The quiet layer behind a page: a radially-masked hairline grid, and one soft pool of brand
 * light that FOLLOWS THE POINTER with a lag — the page acknowledging your hand, not a screensaver.
 *
 * Deliberately not a set of floating purple orbs: static gradient blobs behind a hero are the
 * single most recognisable generated-UI tell. A glow that answers input is an interaction; a glow
 * that just sits there is wallpaper.
 *
 * The glow tracks via `gsap.quickTo` on transforms (no React re-renders) and is gated on
 * `motionOK()` + a fine pointer — touch devices and reduced-motion users get the grid alone,
 * which is a complete composition. Give the parent `relative` and `overflow-hidden`.
 */
export function AmbientBackground({
  intensity = 'subtle',
}: {
  /** `subtle` for tool chrome, `bold` for the signed-out screens where atmosphere is allowed. */
  intensity?: 'subtle' | 'bold';
}): JSX.Element {
  const scope = useRef<HTMLDivElement | null>(null);
  const glowRef = useRef<HTMLDivElement | null>(null);
  const bold = intensity === 'bold';

  useGSAP(
    () => {
      const container = scope.current;
      const glow = glowRef.current;
      if (!container || !glow) return;
      if (!motionOK() || !window.matchMedia('(pointer: fine)').matches) return;

      // Centering must live in GSAP's transform, or the first x/y tween would overwrite it.
      gsap.set(glow, { xPercent: -50, yPercent: -50 });
      const xTo = gsap.quickTo(glow, 'x', { duration: 0.9, ease: 'power3.out' });
      const yTo = gsap.quickTo(glow, 'y', { duration: 0.9, ease: 'power3.out' });
      const show = gsap.quickTo(glow, 'opacity', { duration: 0.5, ease: 'power2.out' });

      // The container is pointer-transparent, so the pointer is read from the window and mapped
      // into its box — the glow still lines up when the page around it scrolls.
      const move = (event: PointerEvent): void => {
        const rect = container.getBoundingClientRect();
        const inside =
          event.clientX >= rect.left &&
          event.clientX <= rect.right &&
          event.clientY >= rect.top &&
          event.clientY <= rect.bottom;
        show(inside ? 1 : 0);
        if (inside) {
          xTo(event.clientX - rect.left);
          yTo(event.clientY - rect.top);
        }
      };
      const leave = (): void => {
        show(0);
      };

      window.addEventListener('pointermove', move, { passive: true });
      document.documentElement.addEventListener('pointerleave', leave);
      return () => {
        window.removeEventListener('pointermove', move);
        document.documentElement.removeEventListener('pointerleave', leave);
        gsap.killTweensOf(glow);
      };
    },
    { scope },
  );

  return (
    <div ref={scope} aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className={`absolute inset-0 ${bold ? 'opacity-100' : 'opacity-60'}`}
        style={{
          backgroundImage:
            'linear-gradient(rgb(var(--ff-edge) / 0.5) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--ff-edge) / 0.5) 1px, transparent 1px)',
          backgroundSize: '56px 56px',
          maskImage: 'radial-gradient(ellipse 75% 55% at 50% 0%, black 25%, transparent 100%)',
          WebkitMaskImage:
            'radial-gradient(ellipse 75% 55% at 50% 0%, black 25%, transparent 100%)',
        }}
      />
      {/* Packets riding the grid — the layer that actually moves. Sits above the static grid and
          below the pointer glow, so the glow reads as light cast over the field. */}
      <AmbientCanvas intensity={intensity} />

      {/* The pointer's pool of light. Positioned by transform only; opacity managed by GSAP. */}
      <div
        ref={glowRef}
        className="absolute left-0 top-0 h-[36rem] w-[36rem] rounded-full opacity-0"
        style={{
          background: `radial-gradient(circle, rgb(var(--ff-brand) / ${
            bold ? 0.09 : 0.06
          }), transparent 60%)`,
        }}
      />
    </div>
  );
}
