import { useLayoutEffect, useRef } from 'react';

type Axis = { position: number; velocity: number };

// Exact step of a critically damped spring, independent of the display's refresh rate.
function glide(axis: Axis, target: number, seconds: number) {
  const rate = 22;
  const offset = axis.position - target;
  const impulse = axis.velocity + rate * offset;
  const decay = Math.exp(-rate * seconds);
  axis.position = target + (offset + impulse * seconds) * decay;
  axis.velocity = (axis.velocity - rate * impulse * seconds) * decay;
}

/** Keep one highlight moving between the selected buttons, including wrapped rows. */
export function SelectionIndicator({ value, continuous = false }: { value: string; continuous?: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  const axes = useRef<Axis[]>([]);
  const previousTick = useRef(0);
  useLayoutEffect(() => {
    const marker = ref.current;
    const group = marker?.parentElement;
    const selected = group?.querySelector<HTMLElement>(':scope > :is([aria-current="page"], [aria-selected="true"], [aria-pressed="true"])');
    if (!marker || !group || !selected) return;

    let frame = 0;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const align = () => {
      if (!selected.offsetWidth) return;
      const target = [selected.offsetLeft, selected.offsetTop, selected.offsetWidth, selected.offsetHeight];
      const [x, y, width, height] = target;
      marker.style.width = width + 'px';
      marker.style.height = height + 'px';
      if (continuous) {
        cancelAnimationFrame(frame);
        const paint = () => {
          const [left, top, w, h] = axes.current;
          marker.style.transform = `translate3d(${left.position}px,${top.position}px,0) scale(${w.position / width},${h.position / height})`;
        };
        const settle = () => {
          axes.current = target.map(position => ({ position, velocity: 0 }));
          previousTick.current = 0;
          paint();
        };
        const tick = (now: number) => {
          // A slow page mount should leave a visible glide on the next painted frame.
          const dt = Math.min(1 / 30, Math.max(0, (now - previousTick.current) / 1000));
          previousTick.current = now;
          if (motion.matches) { settle(); return; }
          axes.current.forEach((axis, i) => glide(axis, target[i], dt));
          if (axes.current.every((a, i) => Math.abs(a.position - target[i]) < .15 && Math.abs(a.velocity) < 3)) {
            settle(); return;
          }
          paint();
          frame = requestAnimationFrame(tick);
        };
        if (!axes.current.length || motion.matches) settle();
        else {
          paint();
          previousTick.current ||= performance.now();
          frame = requestAnimationFrame(tick);
        }
      } else {
        marker.style.transform = `translate(${x}px, ${y}px)`;
      }
      if (!group.hasAttribute('data-selection-ready')) {
        marker.getBoundingClientRect(); // Place the first highlight before enabling movement.
        group.setAttribute('data-selection-ready', '');
      }
    };
    align();
    const observer = new ResizeObserver(align);
    for (const child of group.children) {
      if (child !== marker) observer.observe(child);
    }
    observer.observe(group);
    if (continuous) motion.addEventListener('change', align);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      if (continuous) motion.removeEventListener('change', align);
    };
  }, [value, continuous]);
  return <span ref={ref} className="selection-indicator" data-continuous={continuous || undefined} aria-hidden="true" />;
}
