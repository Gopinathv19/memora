"use client";

import { useEffect, useRef, useState } from "react";

/*
 * Small motion helpers for the dashboard. No animation library: a number that
 * counts toward its value is a requestAnimationFrame loop, and everything else
 * is CSS (see globals.css). Both honour `prefers-reduced-motion`.
 */

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );
}

/**
 * Ease a number toward `target`.
 *
 * It starts from wherever the previous animation had reached, so changing the
 * reporting window glides from the old figure to the new one instead of
 * snapping back to zero first.
 */
export function useCountUp(target: number, duration = 800): number {
  const [value, setValue] = useState(0);
  const from = useRef(0);

  useEffect(() => {
    if (prefersReducedMotion() || from.current === target) {
      from.current = target;
      setValue(target);
      return;
    }
    const start = performance.now();
    const origin = from.current;
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 4); // easeOutQuart: fast, then settles
      const next = origin + (target - origin) * eased;
      from.current = next;
      setValue(next);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, duration]);

  return value;
}

/** A number that counts up to `value`. `format` renders each in-between frame. */
export function AnimatedNumber({
  value,
  format = (n: number) => n.toLocaleString(),
  duration,
}: {
  value: number;
  format?: (n: number) => string;
  duration?: number;
}) {
  const current = useCountUp(value, duration);
  return <>{format(Math.round(current))}</>;
}

/** True one frame after mount: lets CSS transitions run from their "from" state. */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  return mounted;
}
