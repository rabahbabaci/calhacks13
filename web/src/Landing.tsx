// Landing page for signed-out visitors: one hero. <Placeholder> marks a slot for a real logo.
import { useEffect, useRef, useState } from "react";

function Placeholder({ label, ratio = "16 / 9", round = false, className = "" }: {
  label: string;
  ratio?: string;
  round?: boolean;
  className?: string;
}) {
  return (
    <div
      className={"ph" + (round ? " ph-round" : "") + (className ? " " + className : "")}
      style={{ aspectRatio: ratio }}
      role="img"
      aria-label={label + " (placeholder)"}
    >
      <span>{label}</span>
    </div>
  );
}

/** A real picture in a slot: cropped to the slot, fading in once it is loaded and on screen. */
function Photo({ src, alt, ratio = "4 / 3", focus = "center", className = "" }: {
  src: string;
  alt: string;
  ratio?: string;
  focus?: string;
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const el = box.current;
    if (!el || !("IntersectionObserver" in window)) {
      setSeen(true);
      return;
    }
    const watch = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setSeen(true);
        watch.disconnect();
      }
    }, { threshold: 0.2 });
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  return (
    <div ref={box} className={"lp-photo" + (seen && loaded ? " is-in" : "") + (className ? " " + className : "")} style={{ aspectRatio: ratio }}>
      <img src={src} alt={alt} decoding="async" style={{ objectPosition: focus }} onLoad={() => setLoaded(true)} />
    </div>
  );
}

export function Landing({ onStart }: { onStart: () => void }) {
  return (
    <div className="landing">
      <header className="lp-nav">
        <span className="lp-brand">
          <Placeholder label="Logo" ratio="1 / 1" round className="lp-logo" />
          <span>Goldilocks</span>
        </span>
        <button type="button" className="lp-btn lp-btn-small" onClick={onStart}>Sign in</button>
      </header>

      <main>
        <section className="lp-hero">
          <div className="lp-sun" aria-hidden="true" />
          <div className="lp-hero-text">
            <p className="lp-kicker">For renters</p>
            <h1>Every home should be just right.</h1>
            <p className="lp-lede">
              Film a walkthrough. Goldilocks spots the hazards, explains the health risks and your rights,
              and walks you through every step you need to take to get them fixed.
            </p>
            <div className="lp-actions">
              <button type="button" className="lp-btn" onClick={onStart}>Get started</button>
            </div>
            <p className="lp-fine">General information, not legal or medical advice.</p>
          </div>
          <Photo
            src="/images/tenant-filming.jpg"
            alt="A tenant filming her living room with her phone"
            focus="38% 45%"
            className="lp-hero-media"
          />
        </section>
      </main>
    </div>
  );
}
