"""Public-record sources for the property agent. Works for any US address.

find_datasets / query_dataset  Socrata open-data portals (NYC, Chicago, SF, LA, Seattle, Cincinnati, ...)
find_layers / query_layer      ArcGIS feature layers (county parcels, city code cases, ...)
hud_assisted                   HUD multifamily-assisted buildings: inspection score, troubled status, manager
accela                         Accela Citizen Access code cases and permits (Oakland and many other cities), via Apify
web / opinions                 Apify RAG Web Browser and CourtListener, shared with the legal agent

Every call is stored under a data id (D1, D2, ...) with its raw text, so the validator
can check that a saved record really came from what was fetched.
"""
import importlib.util
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path


def _load_legal_research():
    """Reuse agents/legal/research.py without putting agents/legal on sys.path (its module
    names, like validate, would shadow this agent's)."""
    path = Path(__file__).resolve().parents[1] / "legal" / "research.py"
    spec = importlib.util.spec_from_file_location("legal_research", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


Research = _load_legal_research().Research

TIMEOUT = 45
MAX_RESULT_CHARS = 12000
SOCRATA_CATALOG = "https://api.us.socrata.com/api/catalog/v1"
ARCGIS_SEARCH = "https://www.arcgis.com/sharing/rest/search"
HUD_ASSISTED = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/MULTIFAMILY_PROPERTIES_ASSISTED/FeatureServer/0"
HUD_FIELDS = [
    "PROPERTY_NAME_TEXT", "ADDRESS_LINE1_TEXT", "STD_CITY", "STD_ZIP5", "TOTAL_UNIT_COUNT", "PROPERTY_CATEGORY_NAME",
    "REAC_LAST_INSPECTION_SCORE", "REAC_LAST_INSPECTION_DATE", "TROUBLED_STATUS_UPDATE_DATE", "LAST_CRITICAL_DATE_DESC",
    "MGMT_AGENT_ORG_NAME", "MGMT_CONTACT_FULL_NAME", "MGMT_CONTACT_MAIN_PHN_NBR", "MGMT_CONTACT_ADDRESS_LINE1",
    "MGMT_CONTACT_CITY_NAME", "MGMT_CONTACT_STATE_CODE", "EXPIRATION_DATE1",
]
ACCELA_MODULES = {"Enforcement", "Building", "Planning", "Fire", "Housing", "Permits", "Licenses"}
ACCELA_PAGE_FUNCTION = r"""
async function pageFunction(context) {
  const { page, request } = context;
  const { number, street } = request.userData;
  const f = 'ctl00_PlaceHolderMain_generalSearchForm_';
  await page.waitForSelector('#' + f + 'txtGSStreetName', { timeout: 30000 });
  await page.fill('#' + f + 'txtGSNumber_ChildControl0', number);
  await page.fill('#' + f + 'txtGSStreetName', street);
  await page.click('#ctl00_PlaceHolderMain_btnNewSearch');
  await page.waitForTimeout(8000);
  const grab = () => page.$$eval('table[id*="gdvPermitList"] tr', trs =>
    trs.map(tr => Array.from(tr.querySelectorAll('td,th')).map(td => td.innerText.trim())).filter(r => r.some(c => c)));
  let rows = await grab();
  // The results grid shows 10 rows a page; read up to 3 pages.
  for (let p = 2; p <= 3; p++) {
    const next = await page.$('table[id*="gdvPermitList"] a:has-text("Next")');
    if (!next) break;
    await next.click();
    await page.waitForTimeout(5000);
    rows = rows.concat((await grab()).slice(1));
  }
  const detail = await page.$eval('body', b => (b.innerText.match(/(no records|returned no results)[^\n]*/i) || [''])[0]).catch(() => '');
  return { rows: rows.slice(0, 60), note: detail };
}
"""

DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9-]+\.)+[a-z]{2,}$")
DATASET_ID = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")
LAYER_PATH = re.compile(r"/(FeatureServer|MapServer)/\d+$")


class SourceError(RuntimeError):
    pass


def _get_json(url, params=None):
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    req = urllib.request.Request(full, headers={"User-Agent": "habitability-property-agent", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}: {e.read().decode('utf-8', 'replace')[:300]}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise SourceError(f"could not reach {urllib.parse.urlsplit(url).netloc}: {e}") from None


def check_domain(domain):
    """A public hostname: no scheme, path, port, IP address or localhost."""
    domain = (domain or "").strip().lower()
    if not DOMAIN.match(domain) or domain.endswith(".local") or domain.endswith(".internal"):
        raise SourceError(f"not a public hostname: {domain!r}")
    return domain


def check_layer_url(url):
    """An https ArcGIS layer URL (…/FeatureServer/N or …/MapServer/N) on a public hostname."""
    parts = urllib.parse.urlsplit((url or "").strip())
    if parts.scheme != "https" or parts.query or parts.fragment or not LAYER_PATH.search(parts.path):
        raise SourceError("layer url must be https://host/.../FeatureServer/<n> or .../MapServer/<n>")
    try:
        ipaddress.ip_address(parts.hostname or "")
        raise SourceError("layer url must use a hostname, not an IP address")
    except ValueError:
        pass
    check_domain(parts.hostname)
    return f"https://{parts.hostname}{parts.path}"


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class Sources:
    def __init__(self, apify_token, courtlistener_token=None, socrata_token=None):
        self.web = Research(apify_token, courtlistener_token)
        self.apify = self.web.apify
        self.socrata_token = socrata_token
        self.results = {}  # data id -> raw text the model saw

    def keep(self, label, payload):
        """Store a result under the next data id and return (id, text for the model)."""
        data_id = f"D{len(self.results) + 1}"
        text = json.dumps(payload, ensure_ascii=False, default=str)
        self.results[data_id] = text
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + " …[truncated; narrow the query]"
        return data_id, f"data_id {data_id} ({label}):\n{text}"

    # ---- Socrata ----

    def find_datasets(self, query, domain=None):
        params = {"q": query, "only": "dataset", "limit": 10}
        if domain:
            params["domains"] = check_domain(domain)
        body = _get_json(SOCRATA_CATALOG, params)
        out = []
        for r in body.get("results", []):
            res = r.get("resource", {})
            out.append({
                "domain": r.get("metadata", {}).get("domain"),
                "dataset_id": res.get("id"),
                "name": res.get("name"),
                "updated": (res.get("data_updated_at") or "")[:10],
                "columns": (res.get("columns_field_name") or [])[:40],
            })
        return out

    def query_dataset(self, domain, dataset_id, where, select=None, order=None, limit=50):
        if not DATASET_ID.match(dataset_id or ""):
            raise SourceError(f"not a Socrata dataset id: {dataset_id!r}")
        params = {"$where": where, "$limit": str(max(1, min(int(limit or 50), 100)))}
        if select:
            params["$select"] = select
        if order:
            params["$order"] = order
        if self.socrata_token:
            params["$$app_token"] = self.socrata_token
        return _get_json(f"https://{check_domain(domain)}/resource/{dataset_id}.json", params)

    # ---- ArcGIS ----

    def find_layers(self, query):
        body = _get_json(ARCGIS_SEARCH, {"q": f"{query} type:\"Feature Service\"", "f": "json", "num": 10})
        return [
            {"title": r.get("title"), "owner": r.get("owner"), "url": r.get("url"), "modified": r.get("modified")}
            for r in body.get("results", [])
            if r.get("url")
        ]

    def layer_fields(self, url):
        body = _get_json(check_layer_url(url), {"f": "json"})
        if "error" in body:
            raise SourceError(f"ArcGIS error: {body['error'].get('message')}")
        return {"name": body.get("name"), "fields": [f.get("name") for f in body.get("fields", [])][:80]}

    def query_layer(self, url, where, out_fields="*", limit=25):
        body = _get_json(f"{check_layer_url(url)}/query", {
            "where": where,
            "outFields": out_fields or "*",
            "returnGeometry": "false",
            "resultRecordCount": str(max(1, min(int(limit or 25), 100))),
            "f": "json",
        })
        if "error" in body:
            raise SourceError(f"ArcGIS error: {body['error'].get('message')}")
        return [f.get("attributes", {}) for f in body.get("features", [])]

    # ---- HUD ----

    def hud_assisted(self, geo):
        """HUD-assisted multifamily buildings at this address (matched on ZIP and house number)."""
        if not geo or not geo.get("zip") or not geo.get("house_number"):
            return []
        where = f"STD_ZIP5 = {sql_literal(geo['zip'])} AND STD_ADDR LIKE {sql_literal(geo['house_number'] + ' %')}"
        rows = self.query_layer(HUD_ASSISTED, where, ",".join(HUD_FIELDS), 10)
        street = (geo.get("street_name") or "").split(" ")[-1].lower()
        return [r for r in rows if street and street in str(r.get("ADDRESS_LINE1_TEXT", "")).lower()]

    # ---- Accela ----

    def accela(self, agency, module, house_number, street_name):
        """Search an Accela Citizen Access portal (aca-prod.accela.com/<AGENCY>) by street address."""
        agency = (agency or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{2,30}", agency):
            raise SourceError(f"not an Accela agency code: {agency!r}")
        if module not in ACCELA_MODULES:
            raise SourceError(f"module must be one of {sorted(ACCELA_MODULES)}")
        url = f"https://aca-prod.accela.com/{agency}/Cap/CapHome.aspx?module={module}&TabName={module}"
        run = self.apify.actor("apify/playwright-scraper").call(
            run_input={
                "startUrls": [{"url": url, "userData": {"number": str(house_number), "street": str(street_name)}}],
                "pageFunction": ACCELA_PAGE_FUNCTION,
                "maxRequestsPerCrawl": 1,
                "maxRequestRetries": 1,
                "headless": True,
            },
            wait_duration=timedelta(seconds=240),
            logger=None,
        )
        if run is None or "SUCCEEDED" not in str(run.status):
            raise SourceError(f"Accela search ended {run.status if run else 'without a run'}")
        items = list(self.apify.dataset(run.default_dataset_id).iterate_items())
        if not items:
            raise SourceError("Accela search returned nothing (wrong agency code or the portal changed)")
        records = parse_accela_rows(items[0].get("rows") or [])
        if not records:
            return {"portal": url, "records": [], "note": items[0].get("note") or "no records"}
        return {"portal": url, "records": records}


PAGER_CELLS = {"< Prev", "Next >", "Prev", "Next", "..."}


def parse_accela_rows(rows):
    """Turn the Accela results grid into records. The grid starts with a "Showing 1-10 of N"
    banner, repeats its header on each page, and ends each page with a pager row."""
    start = next((i for i, r in enumerate(rows) if "Record Number" in r), None)
    if start is None:
        return []
    header = [h or f"col{i}" for i, h in enumerate(rows[start])]
    records = []
    for r in rows[start + 1:]:
        if any(c in PAGER_CELLS for c in r):
            continue
        rec = {k: v for k, v in zip(header, r) if v and not k.startswith("col")}
        if not rec.get("Record Number") or rec["Record Number"] == "Record Number":
            continue
        if "Description" in rec:
            rec["Description"] = rec["Description"][:240]
        records.append(rec)
    return records
