// Landing page for signed-out visitors: one hero.
import { useEffect, useRef, useState } from "react";

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
          <img className="lp-logo" src="/logo.svg" alt="" width={36} height={36} />
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
              Film a walkthrough of your rental. Goldilocks finds the hazards, cites the health risks and the law
              that applies, and gets a formal notice to your landlord from your own email.
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

        <section className="lp-section lp-how" aria-labelledby="lp-how-h">
          <p className="lp-kicker">How it works</p>
          <h2 id="lp-how-h">Three steps. You decide each one.</h2>
          <ol className="lp-steps">
            <li>
              <span className="lp-num">1</span>
              <h3>Film</h3>
              <p>Walk through your home with your phone. Goldilocks watches the video and marks what it sees: mold, leaks, peeling paint, pests, wiring, missing heat.</p>
            </li>
            <li>
              <span className="lp-num">2</span>
              <h3>Confirm</h3>
              <p>Every finding is placed on a 3D map of your home. You confirm what is real. Nothing happens on a guess.</p>
            </li>
            <li>
              <span className="lp-num">3</span>
              <h3>Send</h3>
              <p>For each confirmed problem you get the health risk, your landlord's legal duty, and a written notice ready to send from your Gmail. Replies are tracked, and the next step follows.</p>
            </li>
          </ol>
        </section>

        <section className="lp-section lp-shot" aria-labelledby="lp-shot-h">
          <div className="lp-shot-text">
            <p className="lp-kicker">Your case, in one place</p>
            <h2 id="lp-shot-h">Always know what happens next.</h2>
            <p className="lp-lede">
              One screen shows where your case stands: what was found, what you confirmed, when the notice went out,
              whether the landlord answered, and the one thing to do now.
            </p>
            <ul className="lp-checks">
              <li>3D map of your home with each hazard in its room</li>
              <li>Health notes cited to CDC, EPA and CDPH pages</li>
              <li>The landlord's duty under California and Oakland law, with the statute</li>
              <li>Written notice from your own email, replies tracked, legal aid and the city complaint when needed</li>
            </ul>
          </div>
          <div className="lp-shot-media">
            <Photo src="/images/case-screen.png" alt="The Goldilocks home dashboard: next action, a five-step timeline, and the hazards found" ratio="1696 / 1368" className="lp-shot-case" />
            <Photo src="/images/model-screen.png" alt="A 3D model of an apartment with three hazards marked in their rooms" ratio="1692 / 696" className="lp-shot-model" />
          </div>
        </section>

        <section className="lp-section lp-proof" aria-label="Sources">
          <p className="lp-proof-line">Every claim cites its source. Anything without one is dropped before you see it.</p>
          <ul className="lp-sources">
            <li>CDC</li>
            <li>EPA</li>
            <li>CDPH</li>
            <li>California Civil Code</li>
            <li>Oakland Municipal Code</li>
            <li>City code enforcement records</li>
          </ul>
        </section>

        <section className="lp-section lp-cta" aria-labelledby="lp-cta-h">
          <h2 id="lp-cta-h">You stay in control.</h2>
          <p className="lp-lede">
            You confirm every finding. Nothing is sent or filed until you press the button. Goldilocks gives general
            information, not legal or medical advice.
          </p>
          <div className="lp-actions">
            <button type="button" className="lp-btn" onClick={onStart}>Get started</button>
          </div>
        </section>
      </main>
      <footer className="lp-foot">
        <span>Goldilocks · Built at Cal Hacks 13.0</span>
        <span>Oakland and California today. More cities as the law packs are verified.</span>
      </footer>
    </div>
  );
}
