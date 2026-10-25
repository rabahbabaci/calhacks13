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
};

export type Tile = {
  id: string;
  title: string;
  kind: "email" | "form" | "phone";
  status: string;
  needs: string[];
  changed: string;
};

export type Home = {
  needs_onboarding?: boolean;
  name?: string;
  address?: string;
  fixture?: boolean;
  layout?: { rooms: Room[] };
  hazards?: Hazard[];
  tiles?: Tile[];
  not_visible?: string[];
  runs?: Record<string, string>;
  tracking?: { messages: Message[]; evidence: { evidence_id: string; kind: string; at: string }[] };
};

export type Room = {
  id: string;
  name: string;
  polygon: number[][];
  height: number;
  door: { wall: string; width: number };
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

export type Me = { signed_in: boolean; dev_login: boolean; google: boolean; case_id?: string; name?: string; email?: string };

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
  onboard: (address: string, file: File | null) => {
    const body = new FormData();
    body.set("address", address);
    if (file) body.set("video", file);
    return call<Home>("/api/onboarding", { method: "POST", body });
  },
  hazard: (id: string) => call<HazardDetail>("/api/hazards/" + encodeURIComponent(id)),
  confirm: (id: string) => call<HazardDetail>("/api/hazards/" + encodeURIComponent(id) + "/confirm", { method: "POST" }),
  clinics: (id: string) => call<Clinics>("/api/hazards/" + encodeURIComponent(id) + "/clinics"),
  preview: (id: string) => call<{ id: string; title: string; kind: string; preview: Preview }>("/api/tiles/" + encodeURIComponent(id) + "/preview", { method: "POST" }),
  send: (id: string, to: string) =>
    call<{ status: string; changed: string }>("/api/tiles/" + encodeURIComponent(id) + "/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to }),
    }),
  reply: (who: string, body: string) =>
    call("/api/tiles/log-reply/reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ who, body }),
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
    health: { status: string; risks: string[]; precautions: string[]; see_a_doctor_if: string; disclaimer: string };
    landlord: {
      status: string;
      company: string;
      initials: string;
      logo_url: string;
      open: number;
      closed: number;
      records: { id: string; date: string; status: string; title: string; fix: string; closed: boolean }[];
    };
    law: { status: string; duties: { text: string; cites: string[] }[]; options: { text: string; cites: string[] }[]; disclaimer: string };
  };
};

export type Clinics = {
  note: string;
  reason: string;
  search_url: string;
  clinics: { name: string; address: string; distance_mi: number | null; maps: string }[];
};
