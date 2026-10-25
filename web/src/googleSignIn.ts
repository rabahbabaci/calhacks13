const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";
const MAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly";
const MAIL_TOKEN_KEY = "home_gmail_token";
const MAIL_EXP_KEY = "home_gmail_exp";

export function googleRedirectUri() {
  return window.location.origin;
}

export function saveMailToken(token: string, expiresIn?: string | number) {
  if (!token) return;
  const seconds = Number(expiresIn);
  sessionStorage.setItem(MAIL_TOKEN_KEY, token);
  sessionStorage.setItem(MAIL_EXP_KEY, String(Date.now() + ((seconds > 0 ? seconds : 3600) - 60) * 1000));
}

export function storedMailToken() {
  const token = sessionStorage.getItem(MAIL_TOKEN_KEY) || "";
  const exp = Number(sessionStorage.getItem(MAIL_EXP_KEY) || 0);
  if (!token || Date.now() >= exp) return "";
  return token;
}

export function clearMailToken() {
  sessionStorage.removeItem(MAIL_TOKEN_KEY);
  sessionStorage.removeItem(MAIL_EXP_KEY);
}

export function takeGoogleReturn() {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const query = new URLSearchParams(window.location.search);
  const token = hash.get("id_token") || query.get("id_token") || "";
  const access = hash.get("access_token") || query.get("access_token") || "";
  const expires = hash.get("expires_in") || query.get("expires_in") || "";
  const error = hash.get("error_description") || hash.get("error") || query.get("error_description") || query.get("error") || "";
  if (access) saveMailToken(access, expires);
  if (token || error || access) {
    window.history.replaceState(null, "", window.location.pathname);
  }
  return { token, error: describeGoogleError(error) };
}

export function describeGoogleError(error: string) {
  const text = decodeURIComponent((error || "").replace(/\+/g, " "));
  if (!text) return "";
  if (/access_denied|verification|tested|testers|403/i.test(text)) {
    return "Google blocked this sign-in because Home is still in testing. In Google Cloud, open APIs & Services → OAuth consent screen → Audience, add the exact Gmail account you are using as a test user, then try Continue with Google again.";
  }
  return text;
}

export function requestGoogleMailToken(): Promise<string> {
  const saved = storedMailToken();
  if (saved) return Promise.resolve(saved);
  return new Promise((resolve, reject) => {
    if (!CLIENT_ID) {
      reject(new Error("Google is not configured."));
      return;
    }
    const oauth = window.google?.accounts.oauth2;
    if (!oauth) {
      reject(new Error("Google sign-in is still loading. Try Send again."));
      return;
    }
    const client = oauth.initTokenClient({
      client_id: CLIENT_ID,
      scope: MAIL_SCOPE,
      callback: (response) => {
        if (response.access_token) {
          saveMailToken(response.access_token);
          resolve(response.access_token);
          return;
        }
        reject(new Error(response.error_description || response.error || "Gmail permission was not granted."));
      },
      error_callback: (error) => {
        reject(new Error(error.message || error.type || "Gmail permission was cancelled."));
      },
    });
    client.requestAccessToken({ prompt: saved ? "" : "consent" });
  });
}

export function mailtoHref(to: string, subject: string, body: string) {
  const params = new URLSearchParams({ subject, body });
  return `mailto:${encodeURIComponent(to)}?${params.toString()}`;
}

export function gmailRaw(to: string, subject: string, body: string) {
  const message = [
    `To: ${to}`,
    `Subject: ${subject.replace(/\r?\n/g, " ")}`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=utf-8",
    "",
    body,
  ].join("\r\n");
  const bytes = new TextEncoder().encode(message);
  let binary = "";
  bytes.forEach((code) => {
    binary += String.fromCharCode(code);
  });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function sendThroughGmail(accessToken: string, to: string, subject: string, body: string) {
  const res = await fetch("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + accessToken,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ raw: gmailRaw(to, subject, body) }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = (data as { error?: { message?: string } }).error?.message || "Gmail could not send this email.";
    if (/has not been used|it is disabled/i.test(message)) {
      throw new Error("The Gmail API is disabled on this Google Cloud project.");
    }
    throw new Error(message);
  }
  return data;
}

function headerOf(headers: { name?: string; value?: string }[] | undefined, name: string) {
  const match = (headers || []).find((row) => (row.name || "").toLowerCase() === name);
  return (match?.value || "").trim();
}

function addressList(value: string) {
  return (value.toLowerCase().match(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/g) || []);
}

function parseMailDate(value: string) {
  const stamp = Date.parse(value);
  return Number.isFinite(stamp) ? stamp : Date.now();
}

export async function listLandlordMail(accessToken: string, addresses: string[], after?: Date) {
  const addrs = [...new Set(addresses.map((item) => item.trim().toLowerCase()).filter(Boolean))];
  if (!addrs.length) return [];
  let query = addrs.map((addr) => `(from:${addr} OR to:${addr})`).join(" OR ");
  if (after) {
    const year = after.getUTCFullYear();
    const month = String(after.getUTCMonth() + 1).padStart(2, "0");
    const day = String(after.getUTCDate()).padStart(2, "0");
    query = `(${query}) after:${year}/${month}/${day}`;
  }
  const list = await fetch(
    "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=20&q=" + encodeURIComponent(query),
    { headers: { Authorization: "Bearer " + accessToken } },
  );
  const listed = await list.json().catch(() => ({}));
  if (!list.ok) {
    throw new Error((listed as { error?: { message?: string } }).error?.message || "Gmail could not list landlord mail.");
  }
  const rows = [];
  for (const item of (listed as { messages?: { id: string }[] }).messages || []) {
    const res = await fetch(
      "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + encodeURIComponent(item.id) + "?format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Cc&metadataHeaders=Subject&metadataHeaders=Date",
      { headers: { Authorization: "Bearer " + accessToken } },
    );
    const message = await res.json().catch(() => ({})) as {
      id?: string;
      snippet?: string;
      payload?: { headers?: { name?: string; value?: string }[] };
    };
    if (!res.ok || !message.id) continue;
    const headers = message.payload?.headers || [];
    const from = headerOf(headers, "from");
    const to = [headerOf(headers, "to"), headerOf(headers, "cc")].join(" ");
    const fromAddrs = addressList(from);
    const incoming = addrs.some((addr) => fromAddrs.includes(addr));
    rows.push({
      gmail_id: message.id,
      direction: incoming ? "in" : "out",
      counterparty: incoming ? (fromAddrs[0] || from) : (addressList(to).find((addr) => addrs.includes(addr)) || addrs[0]),
      subject: headerOf(headers, "subject"),
      at: headerOf(headers, "date") || "just now",
      body: (message.snippet || "").trim(),
    });
  }
  if (!after) return rows;
  const start = after.getTime();
  return rows.filter((row) => parseMailDate(row.at) >= start);
}

export function startGoogleSignIn(opts?: { mail?: boolean }) {
  if (!CLIENT_ID) throw new Error("VITE_GOOGLE_CLIENT_ID is not set.");
  const mail = opts?.mail !== false;
  const nonce = crypto.randomUUID().replace(/-/g, "");
  sessionStorage.setItem("home_google_nonce", nonce);
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    redirect_uri: googleRedirectUri(),
    response_type: mail ? "id_token token" : "id_token",
    response_mode: "fragment",
    scope: mail ? `openid email profile ${MAIL_SCOPE}` : "openid email profile",
    include_granted_scopes: "true",
    nonce,
    prompt: mail ? "consent login" : "select_account",
  });
  window.location.assign("https://accounts.google.com/o/oauth2/v2/auth?" + params.toString());
}
