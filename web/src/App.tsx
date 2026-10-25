import { useEffect, useState } from "react";
import { api, type Clinics, type HazardDetail, type Home, type Preview, type Tile } from "./api";
import { Model } from "./Model";

const SEV: Record<string, string> = { low: "var(--low)", moderate: "var(--moderate)", high: "var(--high)", urgent: "var(--urgent)" };

type Screen = "model" | "steps" | "tracking";

export function App() {
  const [signedIn, setSignedIn] = useState(false);
  const [home, setHome] = useState<Home | null>(null);
  const [screen, setScreen] = useState<Screen>("model");
  const [error, setError] = useState("");
  const [address, setAddress] = useState("123 Example St, Oakland, CA 94612");
  const [file, setFile] = useState<File | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<HazardDetail | null>(null);
  const [clinics, setClinics] = useState<Clinics | null>(null);
  const [preview, setPreview] = useState<{ id: string; preview: Preview } | null>(null);
  const [to, setTo] = useState("");
  const [replyWho, setReplyWho] = useState("Landlord");
  const [replyBody, setReplyBody] = useState("");

  async function refresh() {
    const next = await api.home();
    setHome(next);
  }

  useEffect(() => {
    api.me().then((me) => {
      if (!me.signed_in) return;
      setSignedIn(true);
      return refresh();
    }).catch((err) => setError(String(err.message || err)));
  }, []);

  async function login() {
    setError("");
    await api.login();
    setSignedIn(true);
    await refresh();
  }

  async function onboard() {
    setError("");
    const next = await api.onboard(address, file);
    setHome(next);
    setScreen("model");
  }

  async function openHazard(id: string) {
    setOpenId(id);
    setClinics(null);
    setDetail(await api.hazard(id));
  }

  async function confirm() {
    if (!openId) return;
    setDetail(await api.confirm(openId));
    await refresh();
  }

  async function showPreview(tile: Tile) {
    const result = await api.preview(tile.id);
    setPreview({ id: tile.id, preview: result.preview });
    setTo(result.preview.to || "");
  }

  async function send() {
    if (!preview) return;
    await api.send(preview.id, to);
    setPreview(null);
    await refresh();
  }

  const hasCase = Boolean(home && !home.needs_onboarding && home.layout);
  const colorOf = (id: string) => {
    const hazard = home?.hazards?.find((h) => h.hazard_id === id);
    return ({ low: "#3d8b6e", moderate: "#a6842c", high: "#d06a2b", urgent: "#b1332a" })[hazard?.severity || "moderate"] || "#a6842c";
  };
  const labelOf = (id: string) => home?.hazards?.find((h) => h.hazard_id === id)?.label || "Hazard";

  return (
    <div className="app">
      <header className="top">
        <h1>Home</h1>
        {hasCase && (
          <nav className="nav">
            <button aria-current={screen === "model" ? "page" : undefined} onClick={() => setScreen("model")}>Home</button>
            <button aria-current={screen === "steps" ? "page" : undefined} onClick={() => { setPreview(null); setScreen("steps"); }}>Next steps</button>
            <button aria-current={screen === "tracking" ? "page" : undefined} onClick={() => setScreen("tracking")}>Tracking</button>
          </nav>
        )}
      </header>
      <main>
        {error && <p className="error">{error}</p>}
        {!signedIn && (
          <section className="card login">
            <p>See what is going on in the place you rent, then decide what to do next.</p>
            <button className="primary" onClick={login}>Continue with Google</button>
            <p className="note">Google sign-in needs a client id, which is not set. This button signs in a demo tenant on this computer. No password, and nothing is sent to Google.</p>
          </section>
        )}
        {signedIn && home?.needs_onboarding && (
          <section className="card">
            <h2>Your home</h2>
            <label className="field">Address
              <input value={address} onChange={(e) => setAddress(e.target.value)} />
            </label>
            <label className="field">Video of the home
              <input type="file" accept="video/*,image/*" capture="environment"
                onChange={(e) => setFile(e.target.files?.[0] || null)} />
            </label>
            <button className="primary" onClick={onboard}>Build the model</button>
            <p className="note">The file is stored on this computer. The rooms and icons come from a saved walkthrough, so this step does not call Claude.</p>
          </section>
        )}
        {hasCase && screen === "model" && home && (
          <>
            <p className="note">{home.address}. Tap a colored icon, or a room below.</p>
            <Model rooms={home.layout!.rooms} colorOf={colorOf} labelOf={labelOf} onPick={openHazard} />
            <div className="rooms">
              {home.hazards!.map((h) => (
                <button key={h.hazard_id} className="hazard-btn" onClick={() => openHazard(h.hazard_id)}>
                  <span><strong>{h.label}</strong><span>{h.room}</span></span>
                  <em className="pill" style={{ background: SEV[h.severity] }}>{h.severity}</em>
                </button>
              ))}
            </div>
          </>
        )}
        {hasCase && screen === "steps" && home && (
          <div className="tiles">
            {home.tiles!.map((tile) => (
              <article key={tile.id} className="tile">
                <header>
                  <h2>{tile.title}</h2>
                  <span className="status">{tile.status}</span>
                </header>
                {tile.changed && <p className="note">{tile.changed}</p>}
                <button className="ghost" style={{ color: "#1c211e", borderColor: "var(--line)", marginTop: 8 }} onClick={() => showPreview(tile)}>Preview</button>
                {preview?.id === tile.id && (
                  <div className="preview">
                    <PreviewBody preview={preview.preview} to={to} setTo={setTo} replyWho={replyWho} setReplyWho={setReplyWho} replyBody={replyBody} setReplyBody={setReplyBody} />
                    <div className="row">
                      {tile.kind === "email" && tile.id !== "log-reply" && <button className="send" onClick={send}>Send</button>}
                      {tile.id === "log-reply" && (
                        <button className="send" onClick={async () => { await api.reply(replyWho, replyBody); setPreview(null); await refresh(); }}>Save reply</button>
                      )}
                      {tile.kind === "form" && preview.preview.form?.url && (
                        <a className="primary" href={preview.preview.form.url} target="_blank" rel="noreferrer">Open form</a>
                      )}
                      {tile.kind === "form" && (
                        <a className="primary" href={"/api/tiles/" + encodeURIComponent(tile.id) + "/packet.pdf"}>Download</a>
                      )}
                      {tile.kind === "phone" && <a className="primary" href={"tel:" + preview.preview.phone}>Call {preview.preview.phone}</a>}
                      {tile.kind === "form" && tile.status !== "done" && (
                        <button className="ghost" style={{ color: "#1c211e", borderColor: "var(--line)" }} onClick={async () => { await api.filed(tile.id); setPreview(null); await refresh(); }}>I filed this</button>
                      )}
                    </div>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
        {hasCase && screen === "tracking" && home && (
          <section className="card">
            <h2>Evidence</h2>
            <label className="field">Add a photo or clip
              <input type="file" accept="video/*,image/*" capture="environment" onChange={async (e) => {
                const picked = e.target.files?.[0];
                if (picked) setHome(await api.evidence(picked));
              }} />
            </label>
            {(home.tracking?.evidence || []).map((item) => (
              <p key={item.evidence_id} className="msg">{item.evidence_id} · {item.kind} · {item.at}</p>
            ))}
            {(home.tracking?.evidence || []).length === 0 && <p className="note">No extra evidence yet.</p>}
            <h2>Correspondence</h2>
            {(home.tracking?.messages || []).map((m) => (
              <p key={m.id} className="msg">
                {m.direction === "out" ? "To" : "From"} {m.counterparty} · {m.role}<br />
                {m.subject} · {m.status} · {m.at}
                {m.body ? <><br />{m.body}</> : null}
              </p>
            ))}
            {(home.tracking?.messages || []).length === 0 && <p className="note">No messages yet.</p>}
          </section>
        )}
      </main>
      {detail && (
        <div className="sheet-back" onClick={() => setDetail(null)}>
          <aside className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={detail.hazard.label}>
            <button className="close" onClick={() => setDetail(null)}>Close</button>
            {detail.hazard.urgent_message && <p className="urgent">{detail.hazard.urgent_message}</p>}
            <img className="hazard-photo" src={"/hazards/" + detail.hazard.hazard_id.split("|")[0] + ".svg"} alt="" />
            <h2>{detail.hazard.label}</h2>
            <p><em className="pill" style={{ background: SEV[detail.hazard.severity] }}>{detail.hazard.severity}</em></p>
            <section className="block">
              <article className="info-tile">
                <h3>Where <span className="status">{detail.blocks.place.status}</span></h3>
                <p>{detail.hazard.room}</p>
                <p>{detail.hazard.location}</p>
                <p className="cite">Frame {detail.hazard.frame}</p>
              </article>
            </section>
            <section className="block">
              <h3>Health <span className="status">{detail.blocks.health.status}</span></h3>
              {detail.blocks.health.status === "waiting" && <p>Confirm this is in your home. The health notes load after that.</p>}
              {detail.blocks.health.status === "done" && (
                <>
                  {detail.blocks.health.risks.map((text) => (
                    <article key={text} className="info-tile bullet-tile"><i>•</i><p>{text}</p></article>
                  ))}
                  {detail.blocks.health.precautions.map((text) => (
                    <article key={text} className="info-tile bullet-tile"><i>•</i><p>{text}</p></article>
                  ))}
                  {detail.blocks.health.see_a_doctor_if && (
                    <article className="info-tile bullet-tile"><i>•</i><p>{detail.blocks.health.see_a_doctor_if}</p></article>
                  )}
                  <button className="primary" onClick={async () => setClinics(await api.clinics(detail.hazard.hazard_id))}>Find testing nearby</button>
                  <p className="disclaimer">{detail.blocks.health.disclaimer}</p>
                </>
              )}
            </section>
            <section className="block">
              <h3>This landlord <span className="status">{detail.blocks.landlord.status}</span></h3>
              <article className="info-tile landlord-head">
                {detail.blocks.landlord.logo_url
                  ? <img className="logo" src={detail.blocks.landlord.logo_url} alt="" />
                  : <span className="initials">{detail.blocks.landlord.initials}</span>}
                <strong>{detail.blocks.landlord.company}</strong>
              </article>
              <div className="counts">
                <div className="count"><b>{detail.blocks.landlord.open}</b><span>open</span></div>
                <div className="count"><b>{detail.blocks.landlord.closed}</b><span>closed</span></div>
              </div>
              {detail.blocks.landlord.records.length === 0 && <p>No matching public record.</p>}
              <RecordTiles records={detail.blocks.landlord.records} />
            </section>
            <section className="block">
              <h3>The law <span className="status">{detail.blocks.law.status}</span></h3>
              {detail.blocks.law.status === "waiting" && <p>Confirm this is in your home. The citations load after that.</p>}
              {detail.blocks.law.status === "done" && (
                <>
                  <div className="counts">
                    <div className="count"><b>{detail.blocks.law.duties.length}</b><span>duties</span></div>
                    <div className="count"><b>{detail.blocks.law.options.length}</b><span>options</span></div>
                  </div>
                  {detail.blocks.law.duties.map((row) => (
                    <article key={row.text} className="law-tile"><span className="kind">Duty</span><p>{row.text}</p><span className="cite">{row.cites.join(" · ")}</span></article>
                  ))}
                  {detail.blocks.law.options.map((row) => (
                    <article key={row.text} className="law-tile"><span className="kind">Option</span><p>{row.text}</p><span className="cite">{row.cites.join(" · ")}</span></article>
                  ))}
                  <p className="disclaimer">{detail.blocks.law.disclaimer}</p>
                </>
              )}
            </section>
            {!detail.hazard.confirmed && <button className="primary" onClick={confirm}>This is in my home</button>}
          </aside>
        </div>
      )}
      {clinics && (
        <div className="sheet-back" onClick={() => setClinics(null)}>
          <aside className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Nearby testing">
            <button className="close" onClick={() => setClinics(null)}>Close</button>
            <h2>Nearby testing</h2>
            {clinics.reason && <p>{clinics.reason}</p>}
            {clinics.note && <p className="note">{clinics.note}</p>}
            <a className="primary" href={clinics.search_url} target="_blank" rel="noreferrer">Search health centers</a>
            {clinics.clinics.map((c) => (
              <p key={c.name} className="record">{c.name}<br />{c.address}{c.distance_mi != null ? ` · ${c.distance_mi} mi` : ""}<br />
                {c.maps && <a href={c.maps} target="_blank" rel="noreferrer">Map</a>}
              </p>
            ))}
          </aside>
        </div>
      )}
    </div>
  );
}

function RecordTiles({ records }: { records: HazardDetail["blocks"]["landlord"]["records"] }) {
  const [openId, setOpenId] = useState("");
  return (
    <>
      {records.map((record) => {
        const open = openId === record.id;
        return (
          <button
            key={record.id}
            type="button"
            className={"record-tile " + (record.closed ? "closed" : "open")}
            aria-expanded={open}
            onClick={() => setOpenId(open ? "" : record.id)}
          >
            <span>{record.date}</span>
            <strong>{record.title}</strong>
            {open && <p>{record.fix || "No fix recorded."}</p>}
          </button>
        );
      })}
    </>
  );
}

function PreviewBody({
  preview, to, setTo, replyWho, setReplyWho, replyBody, setReplyBody,
}: {
  preview: Preview;
  to: string;
  setTo: (v: string) => void;
  replyWho: string;
  setReplyWho: (v: string) => void;
  replyBody: string;
  setReplyBody: (v: string) => void;
}) {
  return (
    <>
      {preview.note && <p className="note">{preview.note}</p>}
      {preview.subject && <p><b>{preview.subject}</b></p>}
      {preview.body && <p>{preview.body}</p>}
      {preview.filled.map((row) => (
        <div key={row.label} className="filled"><b>{row.label}</b><span>{row.value}</span></div>
      ))}
      {preview.still_needed.length > 0 && (
        <>
          <p>Still yours to fill in</p>
          <ul>{preview.still_needed.map((item) => <li key={item}>{item}</li>)}</ul>
        </>
      )}
      {preview.form && (
        <p>Official form: <a href={preview.form.url} target="_blank" rel="noreferrer">{preview.form.name}</a></p>
      )}
      {preview.steps.length > 0 && (
        <ol className="steps">{preview.steps.map((step) => <li key={step}>{step}</li>)}</ol>
      )}
      {preview.phone && !preview.form && <p>Dials {preview.phone}</p>}
      {preview.subject.startsWith("Repair") && (
        <label className="field">To
          <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="landlord@email.com" />
        </label>
      )}
      {preview.note.startsWith("This records a reply") && (
        <>
          <label className="field">Who <input value={replyWho} onChange={(e) => setReplyWho(e.target.value)} /></label>
          <label className="field">What they said <input value={replyBody} onChange={(e) => setReplyBody(e.target.value)} /></label>
        </>
      )}
    </>
  );
}
