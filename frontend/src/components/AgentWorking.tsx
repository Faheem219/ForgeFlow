import { useId, useRef } from 'react';

import { gsap, motionOK, safeAnimate, useGSAP } from '../lib/gsap';

/**
 * The "the agent is working" indicator: a five-scene SVG timeline that walks the pipeline.
 *
 * Why this and not the `Spinner`: a spinner says "something is happening", which is the right
 * amount of information for a 200ms save. Codegen and design generation take *minutes*, and a
 * bare spinner for that long reads as a hang. Each scene here is one stage of the run — inputs
 * arriving, code being written, tests sweeping through, a deploy landing, the live check closing —
 * so a long wait shows the shape of the work instead of an undifferentiated twirl.
 *
 * Structure follows the GSAP masking demo it is built from: five groups, three SVG masks, and one
 * timeline using functional values (`(i) => [...][i]`) and position parameters to overlap scenes.
 * The mask ids are namespaced with `useId()` because two of these can be on screen at once (build
 * and design panels both mounted) and duplicate ids would cross-wire the masks.
 *
 * Every visual is authored vector — no raster, no network requests — and colours resolve from the
 * brand token, so it themes with the rest of the app in both light and dark.
 */

/** Small inline glyph, encoded as a data URI so `<image>` needs no network and cannot 404. */
function glyph(body: string): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" fill="none" ` +
    `stroke="rgb(129,140,248)" stroke-width="6" stroke-linecap="round" stroke-linejoin="round">` +
    body +
    `</svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

/** Scene 1 — what you hand the agent: a screenshot, a written spec, a prompt. */
const INPUTS = [
  glyph(
    '<rect x="14" y="20" width="72" height="56" rx="8"/><path d="M14 60l20-18 16 14 12-10 24 20"/>',
  ),
  glyph(
    '<rect x="20" y="12" width="60" height="76" rx="8"/><path d="M34 34h32M34 50h32M34 66h18"/>',
  ),
  glyph('<path d="M38 30 20 50l18 20M62 30l18 20-18 20"/>'),
];

/** Scene 4 — what comes back out: a shipped build. */
const OUTPUT = glyph(
  '<path d="M50 14c16 12 24 28 24 44L50 86 26 58c0-16 8-32 24-44Z"/><circle cx="50" cy="48" r="9"/>',
);

export function AgentWorking({
  label = 'Working',
  size = 104,
  className = '',
}: {
  /** Said out loud to screen readers, and shown beside the mark. */
  label?: string;
  size?: number;
  className?: string;
}): JSX.Element {
  const scope = useRef<HTMLDivElement | null>(null);
  const uid = useId().replace(/:/g, '');
  const g2 = `g2mask-${uid}`;
  const g3 = `g3mask-${uid}`;
  const g5 = `g5mask-${uid}`;

  useGSAP(
    () => {
      if (!motionOK()) return;

      safeAnimate('agent working timeline', () => {
        const tl = gsap.timeline({ repeat: -1, repeatDelay: 0.5 });

        // Scene 1 — the inputs arrive, tilt, scatter, and hand off.
        tl.fromTo(
          '.group1',
          { scale: 0.1, transformOrigin: '124 124' },
          { duration: 0.35, scale: 1, ease: 'expo.inOut' },
        )
          .to('.group1', { duration: 1.2, rotate: 15, ease: 'none' }, 0.1)
          .to(
            '.group1 image',
            {
              scale: (i: number) => [0.4, 0.2, 0.3][i],
              x: (i: number) => [0, 135, 100][i],
              y: (i: number) => [90, 24, 124][i],
              ease: 'back',
            },
            0.4,
          )
          .to('.group1 image', { duration: 0.01, opacity: 0, stagger: 0.06 }, 1.1)

          // Scene 2 — code is written: a circle opens over the surface, then wipes away sideways.
          .to(`.${g2} circle`, { duration: 0.4, attr: { r: '124' }, ease: 'circ' }, 1.3)
          .fromTo(
            '.group2',
            { scale: 1, transformOrigin: '124 124' },
            { duration: 1.5, scale: 0.9, ease: 'none' },
            1.3,
          )
          .to(
            `.${g2} circle`,
            { duration: 0.3, attr: { cx: (i: number) => ['+=248', '-=248'][i] }, ease: 'sine.in' },
            2.45,
          )

          // Scene 3 — the test sweep: four masked panels snap open as the group rotates in.
          .fromTo(
            '.group3',
            { transformOrigin: '124 124', rotate: -90 },
            { duration: 0.9, rotate: 0, ease: 'expo' },
            2.6,
          )
          .fromTo(
            `.${g3} rect`,
            {
              transformOrigin: (i: number) => ['0 124', '124 0', '124 124', '248 124'][i],
              scale: 0,
            },
            { duration: 0.4, scale: 1, ease: 'expo', stagger: -0.03 },
            2.6,
          )
          .to('.group3', { duration: 0.01, scale: 0 }, 3.7)

          // Scene 4 — the deploy lands.
          .from('.group4 image', { duration: 0.01, opacity: 0 }, 3.8)
          .fromTo(
            '.group4',
            { transformOrigin: '83 124', rotate: 15, scale: 0.2 },
            { duration: 0.5, rotate: 0, scale: 0.85, ease: 'bounce' },
            3.8,
          )
          .to('.group4 image', { duration: 0.01, opacity: 0 }, 4.7)

          // Scene 5 — the live check closes the loop.
          .fromTo(
            `.${g5} path`,
            { transformOrigin: '124 124', scale: 0 },
            { duration: 0.8, scale: 1, ease: 'expo' },
            4.7,
          )
          .fromTo(
            `.${g5} circle`,
            { transformOrigin: '83 0', scale: 0 },
            { scale: 1, ease: 'expo' },
            4.7,
          );
      });
    },
    { scope },
  );

  return (
    <div
      ref={scope}
      className={`flex flex-col items-center gap-3 ${className}`}
      role="status"
      aria-live="polite"
    >
      <svg
        viewBox="0 0 248 248"
        width={size}
        height={size}
        aria-hidden
        className="overflow-visible text-brand"
      >
        <defs>
          {/* Two circles that open and then sweep apart — scene 2's reveal and wipe. */}
          <mask id={g2} className={g2}>
            <circle cx="124" cy="124" r="0" fill="#fff" />
            <circle cx="124" cy="124" r="0" fill="#fff" />
          </mask>
          {/* Four quadrant panels — scene 3's snap-open sweep. */}
          <mask id={g3} className={g3}>
            <rect x="0" y="62" width="124" height="124" fill="#fff" />
            <rect x="62" y="0" width="124" height="124" fill="#fff" />
            <rect x="62" y="124" width="124" height="124" fill="#fff" />
            <rect x="124" y="62" width="124" height="124" fill="#fff" />
          </mask>
          {/* A shield path plus a badge circle — scene 5's closing reveal. */}
          <mask id={g5} className={g5}>
            <path d="M124 26c40 30 60 70 60 110l-60 70-60-70c0-40 20-80 60-110Z" fill="#fff" />
            <circle cx="83" cy="0" r="52" fill="#fff" />
          </mask>
        </defs>

        {/*
          Always-on base layer, under every scene.

          The five scenes are each hidden at rest — group1 starts at 10% scale, the rest are behind
          masks that open to zero — so the timeline is the *only* thing making this component
          visible. If it stalls for any reason the user is left staring at an empty box during the
          longest waits in the product, which is the worst possible moment for that. This ring and
          its sweep are plain SMIL/CSS: they never depend on GSAP, so there is always something
          on screen saying work is happening.
        */}
        <g className="base" fill="none" stroke="currentColor">
          <circle cx="124" cy="124" r="96" strokeWidth="6" opacity="0.16" />
          <circle
            cx="124"
            cy="124"
            r="96"
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray="150 453"
            opacity="0.85"
            className="motion-safe:animate-spin"
            style={{ transformOrigin: '124px 124px', animationDuration: '1.6s' }}
          />
        </g>

        {/* Scene 1 — the three inputs. */}
        <g className="group1">
          {INPUTS.map((href, i) => (
            <image key={i} href={href} x={i * 62 + 20} y="84" width="80" height="80" />
          ))}
        </g>

        {/* Scene 2 — the code surface, revealed through the circle mask. */}
        <g className="group2" mask={`url(#${g2})`}>
          <rect x="24" y="24" width="200" height="200" rx="28" fill="currentColor" opacity="0.18" />
          <g stroke="currentColor" strokeWidth="9" strokeLinecap="round">
            <path d="M70 96h108M70 124h72M70 152h92" />
          </g>
        </g>

        {/* Scene 3 — the test sweep, revealed through four quadrant panels. */}
        <g className="group3" mask={`url(#${g3})`}>
          <circle cx="124" cy="124" r="92" fill="none" stroke="currentColor" strokeWidth="12" />
          <path
            d="M84 126l28 28 54-58"
            fill="none"
            stroke="currentColor"
            strokeWidth="14"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </g>

        {/* Scene 4 — the deploy. */}
        <g className="group4">
          <image href={OUTPUT} x="44" y="44" width="160" height="160" />
        </g>

        {/* Scene 5 — validated and live. */}
        <g className="group5" mask={`url(#${g5})`}>
          <rect x="0" y="0" width="248" height="248" fill="currentColor" opacity="0.9" />
        </g>
      </svg>

      <p className="text-xs text-fg-muted">{label}…</p>
    </div>
  );
}
