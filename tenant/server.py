"""HTTP API for the tenant app. The browser never calls SpacetimeDB.

Fixture mode stores an uploaded video and builds the model from the saved walkthrough.
It does not call Claude, Gmail, Apify, or the Census geocoder.

    data/.venv/bin/python -m tenant.server
"""
import json
import os
import re
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tenant.auth import google_client_id as env_client_id, verify_google_token  # noqa: E402
from tenant.mailer import MemoryMailer  # noqa: E402
from tenant.pdfdoc import build_pdf  # noqa: E402
from tenant.view import clinics_payload, hazard_payload, home_payload, new_case, tile_by_id  # noqa: E402

MEDIA = ROOT / "tenant" / "out" / "media"
COOKIE = "home={token}; Path=/; HttpOnly; SameSite=Lax"


class AppState:
    def __init__(self, mailer=None, dev_login=None, google_client_id=None, verify_google=None):
        self.mailer = mailer or MemoryMailer()
        self.google_client_id = env_client_id() if google_client_id is None else google_client_id
        self.verify_google = verify_google or verify_google_token
        if dev_login is None:
            # Demo sign-in is the offline fallback: on unless Google is configured or HOME_DEV_LOGIN says otherwise.
            flag = os.environ.get("HOME_DEV_LOGIN")
            dev_login = flag == "1" if flag is not None else not self.google_client_id
        self.dev_login = dev_login
        self.sessions = {}
        self.users = {}
        self.cases = {}

    def user_for(self, token):
        sub = self.sessions.get(token or "")
        return self.users.get(sub) if sub else None

    def sign_in(self, sub, email, name):
        """Create a session for a user. Repeat sign-ins keep the existing user and case."""
        token = secrets.token_urlsafe(24)
        self.sessions[token] = sub
        self.users.setdefault(sub, {"sub": sub, "email": email, "name": name, "case_id": ""})
        return token, self.users[sub]

    def case_for(self, user):
        if not user or not user.get("case_id"):
            return None
        return self.cases.get(user["case_id"])


def _json_bytes(payload, status=200):
    body = json.dumps(payload).encode("utf-8")
    return status, body, "application/json"


def parse_multipart(body, content_type):
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type or "")
    if not match:
        return {}, {}
    boundary = (match.group(1) or match.group(2)).strip().encode("ascii", "replace")
    fields, files = {}, {}
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        data = data.removesuffix(b"\r\n")
        if data.endswith(b"--"):
            data = data[:-2].removesuffix(b"\r\n")
        text = head.decode("latin-1", "replace")
        name_m = re.search(r'name="([^"]+)"', text)
        if not name_m:
            continue
        name = name_m.group(1)
        file_m = re.search(r'filename="([^"]*)"', text)
        if file_m and file_m.group(1):
            files[name] = {"filename": Path(file_m.group(1)).name, "bytes": data}
        else:
            fields[name] = data.decode("utf-8", "replace").strip()
    return fields, files


def _save_media(case, upload, kind):
    MEDIA.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", upload["filename"]) or "upload.bin"
    evidence_id = f"{kind}_{len(case['media']) + 1}"
    path = MEDIA / f"{case['case_id']}_{evidence_id}_{safe}"
    path.write_bytes(upload["bytes"])
    case["media"].append({"evidence_id": evidence_id, "filename": safe})
    return evidence_id


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _token(self):
            cookie = self.headers.get("Cookie") or ""
            match = re.search(r"(?:^|;\s*)home=([^;]+)", cookie)
            return unquote(match.group(1)) if match else ""

        def _read(self):
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _send(self, status, body, content_type, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in extra or []:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _reply(self, status, payload, extra=None):
            status, body, content_type = _json_bytes(payload, status)
            self._send(status, body, content_type, extra)

        def _user_case(self):
            user = state.user_for(self._token())
            if not user:
                self._reply(401, {"error": "Sign in first."})
                return None, None
            return user, state.case_for(user)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path == "/api/me":
                user = state.user_for(self._token())
                google = bool(state.google_client_id)
                if not user:
                    self._reply(200, {"signed_in": False, "dev_login": state.dev_login, "google": google})
                    return
                self._reply(200, {"signed_in": True, "dev_login": state.dev_login, "google": google, "name": user["name"],
                                  "email": user["email"], "case_id": user.get("case_id") or ""})
                return
            user, case = self._user_case()
            if user is None:
                return
            if path == "/api/home":
                if case is None:
                    self._reply(200, {"needs_onboarding": True, "name": user["name"]})
                    return
                self._reply(200, {"needs_onboarding": False, **home_payload(case)})
                return
            hazard = re.fullmatch(r"/api/hazards/([^/]+)", path)
            if hazard:
                if case is None:
                    self._reply(400, {"error": "Add your address first."})
                    return
                payload = hazard_payload(case, hazard.group(1))
                self._reply(404 if payload is None else 200, payload or {"error": "Unknown hazard."})
                return
            clinics = re.fullmatch(r"/api/hazards/([^/]+)/clinics", path)
            if clinics:
                if case is None:
                    self._reply(400, {"error": "Add your address first."})
                    return
                payload = clinics_payload(case, clinics.group(1))
                self._reply(404 if payload is None else 200, payload or {"error": "Unknown hazard."})
                return
            pdf = re.fullmatch(r"/api/tiles/([^/]+)/packet.pdf", path)
            if pdf and case:
                tile = tile_by_id(case, pdf.group(1))
                if tile is None or tile["kind"] != "form":
                    self._reply(404, {"error": "No form packet for that tile."})
                    return
                preview = tile["preview"]
                form = preview.get("form") or {}
                body = build_pdf(
                    tile["title"], preview.get("filled") or [], preview.get("still_needed") or [],
                    preview.get("steps") or [], form.get("url") or "", form.get("name") or "",
                )
                self._send(200, body, "application/pdf")
                return
            self._reply(404, {"error": "Not found."})

        def do_POST(self):
            path = unquote(urlparse(self.path).path)
            body = self._read()
            if path == "/api/dev/login":
                if not state.dev_login:
                    self._reply(404, {"error": "Demo sign-in is off."})
                    return
                token, user = state.sign_in("demo", "demo@localhost", "Demo Tenant")
                self._reply(200, {"ok": True, "name": user["name"]}, [("Set-Cookie", COOKIE.format(token=token))])
                return
            if path == "/api/auth/google":
                if not state.google_client_id:
                    self._reply(404, {"error": "Google sign-in is not set up on this server."})
                    return
                try:
                    credential = (json.loads(body or b"{}").get("credential") or "").strip()
                except (json.JSONDecodeError, AttributeError):
                    credential = ""
                try:
                    info = state.verify_google(credential, state.google_client_id)
                except ValueError as e:
                    self._reply(401, {"error": str(e)})
                    return
                token, user = state.sign_in(info["sub"], info["email"], info["name"])
                self._reply(200, {"ok": True, "name": user["name"]}, [("Set-Cookie", COOKIE.format(token=token))])
                return
            if path == "/api/auth/logout":
                state.sessions.pop(self._token(), None)
                self._reply(200, {"ok": True}, [("Set-Cookie", COOKIE.format(token="") + "; Max-Age=0")])
                return
            user, case = self._user_case()
            if user is None:
                return
            if path == "/api/onboarding":
                fields, files = parse_multipart(body, self.headers.get("Content-Type"))
                address = fields.get("address") or ""
                if not address.strip():
                    self._reply(400, {"error": "Enter the house address."})
                    return
                case = new_case(user["sub"], user["email"], user["name"], address)
                if files.get("video"):
                    _save_media(case, files["video"], "video")
                state.cases[case["case_id"]] = case
                user["case_id"] = case["case_id"]
                self._reply(200, {"needs_onboarding": False, **home_payload(case)})
                return
            if case is None:
                self._reply(400, {"error": "Add your address first."})
                return
            confirm = re.fullmatch(r"/api/hazards/([^/]+)/confirm", path)
            if confirm:
                hid = confirm.group(1)
                if hazard_payload(case, hid) is None:
                    self._reply(404, {"error": "Unknown hazard."})
                    return
                case["confirmed"][hid] = True
                self._reply(200, hazard_payload(case, hid))
                return
            preview = re.fullmatch(r"/api/tiles/([^/]+)/preview", path)
            if preview:
                tile = tile_by_id(case, preview.group(1))
                if tile is None:
                    self._reply(404, {"error": "Unknown step."})
                    return
                self._reply(200, {"id": tile["id"], "title": tile["title"], "kind": tile["kind"], "preview": tile["preview"]})
                return
            send = re.fullmatch(r"/api/tiles/([^/]+)/send", path)
            if send:
                tile = tile_by_id(case, send.group(1))
                if tile is None or tile["kind"] != "email" or tile["id"] == "log-reply":
                    self._reply(404, {"error": "That step does not send email."})
                    return
                try:
                    incoming = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    incoming = {}
                to = (incoming.get("to") or tile["preview"].get("to") or "").strip()
                if not to:
                    self._reply(400, {"error": "Enter the landlord email first."})
                    return
                record = state.mailer.send({"to": to, "subject": tile["preview"]["subject"], "body": tile["preview"]["body"]})
                case["messages"].append({
                    "id": record["gmail_id"],
                    "direction": "out",
                    "counterparty": to,
                    "role": "landlord",
                    "subject": record["subject"],
                    "status": "sent",
                    "gmail_id": record["gmail_id"],
                    "at": "just now",
                    "tile_id": tile["id"],
                })
                updated = tile_by_id(case, tile["id"])
                self._reply(200, {"status": updated["status"], "changed": updated["changed"],
                                  "tracking": {"messages": case["messages"], "evidence": case["evidence"]}})
                return
            reply = re.fullmatch(r"/api/tiles/([^/]+)/reply", path)
            if reply:
                if reply.group(1) != "log-reply":
                    self._reply(404, {"error": "That step does not log a reply."})
                    return
                try:
                    incoming = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    incoming = {}
                who = (incoming.get("who") or "Landlord").strip()
                said = (incoming.get("body") or "").strip()
                if not said:
                    self._reply(400, {"error": "Write what they said."})
                    return
                case["messages"].append({
                    "id": f"in-{len(case['messages']) + 1}",
                    "direction": "in",
                    "counterparty": who,
                    "role": incoming.get("role") or "landlord",
                    "subject": incoming.get("subject") or "Reply",
                    "status": "received",
                    "gmail_id": "",
                    "at": "just now",
                    "tile_id": "log-reply",
                    "body": said,
                })
                self._reply(200, {"tracking": {"messages": case["messages"], "evidence": case["evidence"]}})
                return
            filed = re.fullmatch(r"/api/tiles/([^/]+)/filed", path)
            if filed:
                tile_id = filed.group(1)
                if tile_by_id(case, tile_id) is None:
                    self._reply(404, {"error": "Unknown step."})
                    return
                if tile_id not in case["filed"]:
                    case["filed"].append(tile_id)
                updated = tile_by_id(case, tile_id)
                self._reply(200, {"status": updated["status"], "changed": updated["changed"]})
                return
            if path == "/api/tracking/evidence":
                _fields, files = parse_multipart(body, self.headers.get("Content-Type"))
                upload = files.get("file")
                if not upload:
                    self._reply(400, {"error": "Choose a photo or clip."})
                    return
                evidence_id = _save_media(case, upload, "photo")
                case["evidence"].append({"evidence_id": evidence_id, "kind": "photo", "at": "just now"})
                self._reply(200, home_payload(case))
                return
            self._reply(404, {"error": "Not found."})

    return Handler


def serve(host="127.0.0.1", port=8787, state=None):
    state = state or AppState()
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    return httpd


def main():
    port = int(os.environ.get("HOME_PORT", "8787"))
    state = AppState()
    if state.google_client_id:
        try:
            import google.oauth2.id_token  # noqa: F401
        except ImportError:
            sys.exit("GOOGLE_CLIENT_ID is set but google-auth is missing: data/.venv/bin/pip install -r tenant/requirements.txt")
    httpd = serve(port=port, state=state)
    sign_in = "Google sign-in on" if state.google_client_id else "Google sign-in off (no GOOGLE_CLIENT_ID)"
    print(f"Home API on http://127.0.0.1:{port} (fixture mode, no Claude calls). {sign_in}, demo sign-in {'on' if state.dev_login else 'off'}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
