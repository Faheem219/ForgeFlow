import { useEffect, useRef } from 'react';

import { gsap, motionOK } from '../lib/gsap';

/**
 * The animated layer behind the page: luminous packets travelling the hairline grid.
 *
 * Why packets on a grid rather than drifting orbs — the obvious choice was free-floating dust with
 * a mouse attractor, but that is the default ambient background of every agency site, and it is the
 * same family of thing (soft blurred blobs behind a hero) this UI deliberately removed. ForgeFlow
 * moves work through a pipeline, so the background moves *along the grid the page already draws*:
 * axis-aligned runs, occasional turns at intersections, a fading trail behind each head. It reads
 * as a circuit or a dataflow, which is what the product is.
 *
 * Implementation notes, per the canvas-performance rules:
 *  - One `<canvas>`, not N DOM nodes. GSAP's ticker owns the loop, so this shares the app's clock
 *    (and stops automatically when the tab is hidden) instead of running a second rAF.
 *  - `devicePixelRatio` is capped at 2 — beyond that the buffer cost buys nothing visible.
 *  - Particle count scales with area and is hard-capped, so a large monitor cannot melt a laptop.
 *  - An IntersectionObserver unhooks the ticker entirely when the layer scrolls out of view.
 *  - Under reduced motion the loop never starts and nothing is drawn; the static grid behind this
 *    canvas is a complete composition on its own.
 *
 * Trails are drawn with `destination-out` compositing rather than by painting a translucent
 * background colour: the canvas sits over the page, so it must stay genuinely transparent.
 */

/** Matches the CSS grid in `AmbientBackground`, so packets ride the lines you can actually see. */
const CELL = 56;
const MAX_PARTICLES = 46;
const TURN_CHANCE = 0.18;

interface Packet {
  x: number;
  y: number;
  /** Axis-aligned heading; packets only turn at intersections. */
  dx: -1 | 0 | 1;
  dy: -1 | 0 | 1;
  speed: number;
  /** 0..1 — drives both alpha and head size, so packets fade in and out rather than popping. */
  life: number;
  ttl: number;
}

function readBrandRgb(): [number, number, number] {
  if (typeof window === 'undefined') return [99, 102, 241];
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--ff-brand').trim();
  const parts = raw
    .split(/[\s,]+/)
    .map(Number)
    .filter(Number.isFinite);
  return parts.length >= 3 ? [parts[0], parts[1], parts[2]] : [99, 102, 241];
}

function spawn(width: number, height: number): Packet {
  const horizontal = Math.random() < 0.5;
  // Snap to a grid line on the travel axis so the packet rides a line rather than crossing open space.
  const lane = Math.round((Math.random() * (horizontal ? height : width)) / CELL) * CELL;
  const along = Math.random() * (horizontal ? width : height);
  const ttl = 420 + Math.random() * 520;
  return {
    x: horizontal ? along : lane,
    y: horizontal ? lane : along,
    dx: horizontal ? (Math.random() < 0.5 ? -1 : 1) : 0,
    dy: horizontal ? 0 : Math.random() < 0.5 ? -1 : 1,
    speed: 0.25 + Math.random() * 0.5,
    life: 0,
    ttl,
  };
}

export function AmbientCanvas({ intensity = 'subtle' }: { intensity?: 'subtle' | 'bold' }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !motionOK()) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const peak = intensity === 'bold' ? 0.5 : 0.3;
    let brand = readBrandRgb();
    let width = 0;
    let height = 0;
    let packets: Packet[] = [];
    // Cursor position in canvas space; the attractor that brightens packets as it passes them.
    const pointer = { x: -9999, y: -9999 };

    const resize = (): void => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const target = Math.min(MAX_PARTICLES, Math.round((width * height) / 26000));
      packets = Array.from({ length: Math.max(8, target) }, () => spawn(width, height));
    };

    const onPointer = (event: PointerEvent): void => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = event.clientX - rect.left;
      pointer.y = event.clientY - rect.top;
    };

    const tick = (_time: number, deltaMs: number): void => {
      // Normalised against 60fps so speed is frame-rate independent on a 120Hz display.
      const step = Math.min(deltaMs / 16.67, 3);

      // Fade the previous frame instead of clearing it — this is what leaves the trail, and
      // `destination-out` removes alpha so the canvas stays transparent over the page.
      ctx.globalCompositeOperation = 'destination-out';
      ctx.fillStyle = 'rgba(0,0,0,0.085)';
      ctx.fillRect(0, 0, width, height);
      ctx.globalCompositeOperation = 'lighter';

      for (let i = 0; i < packets.length; i += 1) {
        const p = packets[i];
        p.life += step;
        if (p.life > p.ttl) {
          packets[i] = spawn(width, height);
          continue;
        }

        const wasX = Math.floor(p.x / CELL);
        const wasY = Math.floor(p.y / CELL);

        // Packets accelerate through the cursor's field — the grid-locked analogue of an attractor.
        const distance = Math.hypot(p.x - pointer.x, p.y - pointer.y);
        const pull = distance < 190 ? 1 - distance / 190 : 0;

        p.x += p.dx * p.speed * step * (1 + pull * 2.2);
        p.y += p.dy * p.speed * step * (1 + pull * 2.2);

        // Wrap rather than despawn, so density stays even across the whole field.
        if (p.x < -CELL) p.x = width + CELL;
        if (p.x > width + CELL) p.x = -CELL;
        if (p.y < -CELL) p.y = height + CELL;
        if (p.y > height + CELL) p.y = -CELL;

        // Turn only when a new intersection is crossed, and snap to the line being joined so the
        // path stays exactly on the grid instead of drifting off it over time.
        if (Math.floor(p.x / CELL) !== wasX || Math.floor(p.y / CELL) !== wasY) {
          if (Math.random() < TURN_CHANCE) {
            if (p.dx !== 0) {
              p.x = Math.round(p.x / CELL) * CELL;
              p.dy = Math.random() < 0.5 ? -1 : 1;
              p.dx = 0;
            } else {
              p.y = Math.round(p.y / CELL) * CELL;
              p.dx = Math.random() < 0.5 ? -1 : 1;
              p.dy = 0;
            }
          }
        }

        // Ease in over the first ~12% of life and out over the last ~25%, so nothing pops.
        const ratio = p.life / p.ttl;
        const envelope = Math.min(1, ratio / 0.12, (1 - ratio) / 0.25);
        const alpha = envelope * peak * (0.45 + pull * 0.55);
        const radius = 1.15 + pull * 1.5;
        const [r, g, b] = brand;

        const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, radius * 5);
        glow.addColorStop(0, `rgba(${r},${g},${b},${alpha})`);
        glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius * 5, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = `rgba(${r},${g},${b},${Math.min(1, alpha * 1.9)})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.globalCompositeOperation = 'source-over';
    };

    resize();

    // Only run while on screen. `gsap.ticker` also idles with the tab, so a backgrounded page costs
    // nothing at all.
    let running = false;
    const start = (): void => {
      if (running) return;
      running = true;
      gsap.ticker.add(tick);
    };
    const stop = (): void => {
      if (!running) return;
      running = false;
      gsap.ticker.remove(tick);
    };

    const visibility = new IntersectionObserver(
      ([entry]) => (entry?.isIntersecting ? start() : stop()),
      { threshold: 0 },
    );
    visibility.observe(canvas);

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(canvas);

    // The brand token differs per theme; re-read it whenever the theme attribute is stamped.
    const themeWatcher = new MutationObserver(() => {
      brand = readBrandRgb();
    });
    themeWatcher.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });

    window.addEventListener('pointermove', onPointer, { passive: true });

    return () => {
      stop();
      visibility.disconnect();
      resizeObserver.disconnect();
      themeWatcher.disconnect();
      window.removeEventListener('pointermove', onPointer);
    };
  }, [intensity]);

  return <canvas ref={canvasRef} aria-hidden className="absolute inset-0 h-full w-full" />;
}
