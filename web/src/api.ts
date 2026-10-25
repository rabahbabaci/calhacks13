export type Hazard = {
  hazard_id: string;
  label: string;
  room: string;
  location: string;
  severity: string;
  confirmed: boolean;
  frame: string;
  urgent_message?: string | null;
  evidence_ids: string[];
  photo?: string;
};

export type LandlordRecord = {
  id: string;
  date: string;
  status: string;
  title: string;
  closed: boolean;
  fix: string;
  ref?: string;
};

export type LandlordContact = {
  name: string;
  role: string;
  mailing_address: string;
  email: string;
  phone: string;
  website: string;
};

export type Landlord = {
  company: string;
  initials: string;
  logo_url: string;
  contact?: LandlordContact;
  owner: LandlordContact;
  manager: LandlordContact;
  disclaimer: string;
  summary?: string;
  open: number;
  closed: number;
  records: LandlordRecord[];
};

export type Tile = {
  id: string;
  title: string;
  kind: "email" | "form" | "phone" | "evidence";
  status: string;
  needs: string[];
  changed: string;
  hazard_id?: string;
  why?: string;
};

export type StepGroup = {
  tag: string;
  label: string;
  hazards: { hazard_id: string; room: string; location: string; label: string }[];
  options: { text: string; cites: string[] }[];
};

export type Home = {
  needs_onboarding?: boolean;
  name?: string;
  address?: string;
  fixture?: boolean;
  live?: boolean;
  layout?: { rooms: Room[] };
  shell?: { kind: string; scale: string; source_url: string; square_feet?: number | null };
  hazards?: Hazard[];
  tiles?: Tile[];
  next?: { tile_id: string; kind: string; blurb: string };
  groups?: StepGroup[];
  landlord?: Landlord;
  not_visible?: string[];
  runs?: Record<string, string>;
  analyzing?: boolean;
  errors?: Record<string, string>;
  tracking?: { messages: Message[]; evidence: { evidence_id: string; kind: string; at: string }[] };
};

export type Room = {
  id: string;
  name: string;
  kind?: string;
  polygon: number[][];
  height: number;
  door: { wall?: string; width: number; center?: number; edge?: number };
  hazards: { hazard_id: string; icon: number[] }[];
};

export type Message = {
  id: string;
  direction: string;
  counterparty: string;
  role: string;
  subject: string;
  status: string;
  at: string;
  body?: string;
};

export type Preview = {
  to: string;
  subject: string;
  body: string;
  phone: string;
  filled: { label: string; value: string }[];
  still_needed: string[];
  form: { name: string; url: string; phone: string; email: string; official_form: boolean } | null;
  steps: string[];
  note: string;
};

export type Me = { signed_in: boolean; dev_login: boolean; google: boolean; live?: boolean; case_id?: string; name?: string; email?: string };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "include", ...init });
  const text = await res.text();
  const data = text && !res.headers.get("Content-Type")?.includes("pdf") ? JSON.parse(text) : text;
  if (!res.ok) throw new Error((data && data.error) || res.statusText);
  return data as T;
}

export const api = {
  me: () => call<Me>("/api/me"),
  login: () => call("/api/dev/login", { method: "POST" }),
  google: (credential: string) =>
    call("/api/auth/google", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential }),
    }),
  logout: () => call("/api/auth/logout", { method: "POST" }),
  home: () => call<Home>("/api/home"),
  onboard: (address: string, file: File | null, onProgress?: (fraction: number) => void) => {
    const body = new FormData();
    body.set("address", address);
    if (file) body.set("video", file);
    // XHR instead of fetch so the upload can report progress; a phone video is the slow part.
    return new Promise<Home>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/onboarding");
      xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => { if (onProgress && e.lengthComputable) onProgress(e.loaded / e.total); };
      xhr.onerror = () => reject(new Error("The upload failed. Check the connection and try again."));
      xhr.onload = () => {
        let data: { error?: string } & Home = {};
        try { data = JSON.parse(xhr.responseText || "{}"); } catch { /* not json */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data as Home);
        else reject(new Error(data.error || xhr.statusText || "Upload failed."));
      };
      xhr.send(body);
    });
  },
  hazard: (id: string) => call<HazardDetail>("/api/hazards/" + encodeURIComponent(id)),
  confirm: (id: string) => call<HazardDetail>("/api/hazards/" + encodeURIComponent(id) + "/confirm", { method: "POST" }),
  clinics: (id: string, init?: RequestInit) => call<Clinics>("/api/hazards/" + encodeURIComponent(id) + "/clinics", init),
  preview: (id: string) => call<{ id: string; title: string; kind: string; preview: Preview }>("/api/tiles/" + encodeURIComponent(id) + "/preview", { method: "POST" }),
  send: (id: string, to: string, gmailId = "") =>
    call<{ status: string; changed: string }>("/api/tiles/" + encodeURIComponent(id) + "/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to, gmail_id: gmailId }),
    }),
  mail: (messages: { gmail_id: string; direction: string; counterparty: string; subject: string; at?: string; body?: string }[]) =>
    call<Home>("/api/tracking/mail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    }),
  filed: (id: string) => call("/api/tiles/" + encodeURIComponent(id) + "/filed", { method: "POST" }),
  evidence: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return call<Home>("/api/tracking/evidence", { method: "POST", body });
  },
};

export type HazardDetail = {
  hazard: Hazard;
  blocks: {
    place: { status: string };
    health: { status: string; error?: string; risks: string[]; precautions: string[]; see_a_doctor_if: string; see_a_doctor?: string[]; disclaimer: string };
    landlord: {
      status: string;
      company: string;
      initials: string;
      logo_url: string;
      error?: string;
      open: number;
      closed: number;
      records: { id: string; date: string; status: string; title: string; fix: string; closed: boolean }[];
    };
    law: { status: string; error?: string; duties: { text: string; cites: string[] }[]; options: { text: string; cites: string[] }[]; disclaimer: string };
  };
};

export type ClinicRow = {
  name: string;
  address: string;
  distance_mi: number | null;
  maps: string;
  tests: string;
  assumed?: string;
  insurance: string;
  assumed_insurance?: string;
  funding: string;
  source_url: string;
  lat?: number;
  lon?: number;
};

export type ClinicGroup = {
  tag: string;
  label: string;
  reason: string;
  hazards: { hazard_id: string; room: string; location: string; label: string }[];
  clinics: ClinicRow[];
};

export type Clinics = {
  note: string;
  reason: string;
  search_url: string;
  clinics: ClinicRow[];
  groups?: ClinicGroup[];
  warming?: boolean;
};
