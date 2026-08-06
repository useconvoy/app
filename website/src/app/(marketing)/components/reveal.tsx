"use client";

import { useEffect, useRef } from "react";

/**
 * Settles its children into place the first time they scroll into view.
 *
 * This is the only client component on the marketing pages. It takes children
 * rather than importing what it wraps, so the wrapped subtree is rendered on
 * the server and arrives as output: putting "use client" here draws a
 * boundary downward through imports, never upward through props.
 *
 * The content is always in the server HTML at its final size and is only
 * visually offset, so a reveal can never move the page. If
 * IntersectionObserver is missing the children are shown immediately; nothing
 * here may decide whether content exists.
 */
export function Reveal({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    if (!("IntersectionObserver" in window)) {
      element.dataset.shown = "true";
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          (entry.target as HTMLElement).dataset.shown = "true";
          // Fire once. Leaving the observer attached costs work on every
          // scroll for an element that will never change again.
          observer.unobserve(entry.target);
        }
      },
      // A little past the bottom edge, so a section does not begin settling
      // when only its first pixel is showing.
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" },
    );

    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} data-reveal className={className}>
      {children}
    </div>
  );
}
