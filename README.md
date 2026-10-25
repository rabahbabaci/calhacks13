# Goldilocks

Help for renters living with unsafe housing. A tenant enters their address and films a walkthrough of their home. Claude agents spot hazards like mold, leaks, pests, exposed wiring or missing heat, and place them on a 3D floor plan. For each hazard the tenant confirms, the agents explain the health risk, the landlord's legal duty and the tenant's options, with a source cited for every claim. They also look up the building's public record and the owner or manager listed in it. The app then helps the tenant send a formal written notice from their own Gmail and track the landlord's reply. If the landlord doesn't fix the problem, it points the tenant to local legal aid and the right filing.

Built at Cal Hacks 13.0 (Oct 23–25, 2026) with **Claude**, **SpacetimeDB** and **Apify**.

**Live:** https://www.goldilocks.casa

![Architecture](docs/images/architecture.png)

## How it works

1. **Vision agent** (`agents/vision/`) samples frames from the video and sends them to Claude. Claude reports hazards using the 9 hazard tags in `docs/contracts.md` section 1.
2. **Plan agent** (`agents/plan/`) builds the 3D shell of the home. It uses a published floor plan if it can find one, else boxes sized from a cited square footage, else a fixed schematic. Vision hazards are placed in the matching rooms.
3. **The tenant confirms** each hazard. Nothing downstream runs on a guess.
4. For each confirmed hazard, in parallel:
   - **Health agent** (`agents/health/`) gives health risks, cited to CDC, EPA and CDPH pages that Apify crawled into SpacetimeDB.
   - **Legal agent** (`agents/legal/`) gives landlord duties, tenant options and local legal aid, cited to a statute pack in `data/packs/`.
5. **Property agent** (`agents/property/`) runs once per address. It finds the building's owner or managing company, its contact details, code violations, permits and inspections, using Apify and open-data portals. A complaint on file is not a finding, and the building's record is never treated as the landlord's personal history.
6. **The app** (`web/` + `tenant/`) shows the 3D home, the cited health and legal information, and the next steps:
   - a formal notice to the landlord, sent from the tenant's Gmail;
   - the landlord's replies, read back from Gmail;
   - local legal aid and the right filing if the landlord doesn't act;
   - nearby clinics for testing (lead, mold, allergy).

Every agent's output goes through a validator. It removes any claim that doesn't cite a source the agent actually retrieved.

There are two ways the agents run:
- **The app's live mode** (`HOME_LIVE=1`): the tenant API runs the agents in its own process and keeps the results in memory.
- **The orchestrator** (`integration/orchestrator.py`): it watches SpacetimeDB for confirmed hazards and runs the agents. Each agent records its run in the `agent_run` table as running, then done or failed.

The browser never talks to SpacetimeDB directly. With `HOME_LIVE_SAVE=1`, as on the deployed site, the API writes every run to SpacetimeDB through the same reducers the orchestrator uses. Use one runner per case, not both, or every run happens twice.

## Repo layout

| Path | What it is |
|---|---|
| `spacetimedb/` | SpacetimeDB 2.13 TypeScript module `home` (the database keeps its original name): 7 tables and 10 reducers |
| `data/` | `stdb.py` (Python client for SpacetimeDB), Apify health-source ingest, Census geocoder, legal packs (`data/packs/`) |
| `agents/vision/` | Hazard detection from video or photos. Needs ffmpeg |
| `agents/plan/` | 3D shell of the home from a floor plan or the listing's square footage |
| `agents/health/` | Health info per hazard |
| `agents/legal/` | Legal info and legal aid per hazard (city and state packs, with optional live research) |
| `agents/property/` | Building history and landlord contact for any US address |
| `integration/` | Orchestrator, demo seeding, end-to-end check |
| `tenant/` | Python HTTP API for the app: sign-in, live agent runs, letters and forms, Gmail tracking, nearby clinics |
| `web/` | Vite + React + three.js app, which Capacitor can package as an iPhone app |
| `docs/contracts.md` | The shared data shapes: hazard tags, case input, every agent's output, tables, app screens |
| `docs/stack-findings.md` | Build log: what worked, what broke, measured cost and latency |

## Quick start: the demo app (no API keys)

Needs Python 3.12+ and Node 20+.

```bash
python3 -m venv data/.venv
```

```bash
data/.venv/bin/pip install -r requirements.txt
```

```bash
data/.venv/bin/python -m tenant.server
```

In a second terminal:

```bash
cd web && npm install && npm run dev
```

Open http://127.0.0.1:5173. The API runs on http://127.0.0.1:8787, and Vite forwards `/api` to it.

This is **fixture mode**, the default:
- With no Google client configured, "Continue with Google" starts a local demo session.
- Each demo sign-in is its own private case, and signing out deletes it.
- Rooms and hazards come from the saved walkthrough in `tenant/fixture.py`.
- Nothing calls Claude, Apify, Gmail or the database.

## Live mode: the real agents in the app

Install everything from the full setup below and fill in `.env`. Then start the API with:

```bash
HOME_LIVE=1 data/.venv/bin/python -m tenant.server
```

What runs, and when:
- **After the address is entered:** the property agent looks up the building, and the plan agent builds the shell in the background.
- **If a video is attached:** it goes to the vision agent.
- **After each confirm:** the health and legal agents run for that hazard.

Results stay in the API's memory. `HOME_LIVE_SAVE=1` also saves them to SpacetimeDB. **This spends Claude and Apify credits.**

**Sign-in and Gmail.** For real Google sign-in, set `GOOGLE_CLIENT_ID` in the repo-root `.env` and `VITE_GOOGLE_CLIENT_ID` in `web/.env.local`.
- **Continue with Google** also asks for Gmail access: `gmail.send` to send the notice, and `gmail.readonly` to read the landlord's replies. **Sign in with email** signs in with the Google account only and asks for Gmail later, on Send.
- Once the notice is sent, the app checks Gmail for replies from the landlord every 30 seconds and updates the next steps.
- Without Gmail access (demo tenant, no Google client, or a refused consent), Send opens the same notice in the tenant's mail app.
- `HOME_DEV_LOGIN=1` keeps the demo session available, and `HOME_DEV_LOGIN=0` hides it.

**Nearby clinics.** With no saved results, the clinics screen shows the HRSA health-center search link. To look clinics up live, either run the lookup for one ZIP, or start the API with `HOME_CLINICS_LIVE=1`:

```bash
data/.venv/bin/python -m tenant.clinics_live --zip 94612
```

Either way it uses HRSA's ZIP locator plus up to 8 Apify searches, and it never invents clinics or tests.

**iPhone:** run `npm run build`, then `npx cap add ios` from `web/` (see `web/capacitor.config.json`).

## Full setup: agents and database

### 1. Install

```bash
python3 -m venv data/.venv
```

```bash
data/.venv/bin/pip install -r agents/legal/requirements.txt -r agents/property/requirements.txt -r tenant/requirements.txt Pillow yt-dlp
```

```bash
brew install ffmpeg poppler
```

ffmpeg and Pillow are for the vision agent, and poppler (`pdftoppm`) is for floor-plan PDFs in the plan agent. yt-dlp is only needed to download the demo video for the vision evals.

### 2. Configure `.env`

Create `.env` in the repo root. It is gitignored. Never commit it, and make sure it ends with a newline before you append to it.

```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_TOKEN=apify_api_...
STDB_URL=https://maincloud.spacetimedb.com
```

| Variable | Used by | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | every agent | Claude API key from console.anthropic.com. Live mode and the orchestrator fall back to it when an agent's own key is unset |
| `ANTHROPIC_WORKSPACE_ID` | vision, live mode | Only for user-scoped keys (`sk-ant-usr-...`) |
| `APIFY_TOKEN` | `data/ingest.py`, orchestrator, live mode | From console.apify.com |
| `ANTHROPIC_API_KEY_Legal`, `APIFY_TOKEN_Legal` | legal agent run on its own | See `agents/legal/.env.example` |
| `ANTHROPIC_API_KEY_Property`, `APIFY_TOKEN_Property` | property agent run on its own | See `agents/property/.env.example` |
| `ANTHROPIC_API_KEY_Plan`, `APIFY_TOKEN_Plan` | plan agent | Fall back to the Property keys, then the plain keys. With no Apify token, it fetches pages directly. See `agents/plan/.env.example` |
| `COURTLISTENER_TOKEN_Legal`, `_Property` | legal research, property lawsuit search | Optional, free at courtlistener.com |
| `SOCRATA_APP_TOKEN_Property` | property | Optional, raises open-data rate limits |
| `STDB_URL`, `STDB_DB`, `STDB_TOKEN` | everything that talks to the database | Default is local `http://127.0.0.1:3000`, database `home`. The team database is Maincloud, which needs no token. `_Legal`, `_Property` and `_Plan` versions override these for one agent |
| `LEGAL_RESEARCH=1` | legal | Turns on live web and court research. Off by default: answers come only from the pack, in about 10 s per hazard |
| `LEGAL_DRAFT_PACKS=1` | legal | Lets the agent use city packs still marked as needing verification. For local testing only |
| `HOME_LIVE=1`, `HOME_LIVE_SAVE=1` | tenant API | Run the real agents from the app, and also save their results to SpacetimeDB |
| `HOME_CLINICS_LIVE=1` | tenant API | Look up nearby clinics live through Apify |
| `HOME_PLAN_JSON` | tenant API | Path to a saved plan agent report, to show its shell in the app |
| `GOOGLE_CLIENT_ID`, `HOME_DEV_LOGIN`, `HOME_PORT` | tenant API | Sign-in settings and the port (default 8787) |
| `VITE_GOOGLE_CLIENT_ID` | web (`web/.env.local`) | The same Google client id, for the sign-in button |

### 3. The database

The team database is live on Maincloud (`https://maincloud.spacetimedb.com`, database `home`). It holds 15 health sources and 110 chunks. With `STDB_URL` set as above, you don't need to run anything locally.

To run a local copy instead:
1. Install the [SpacetimeDB CLI](https://spacetimedb.com/install) and start the server:

   ```bash
   spacetime start
   ```

2. In a second terminal, publish the module:

   ```bash
   cd spacetimedb && npm install && spacetime publish --server local home
   ```

3. Back in the repo root, load the health sources from the committed crawl (no Apify call):

   ```bash
   STDB_URL=http://127.0.0.1:3000 data/.venv/bin/python data/ingest.py --from-cache
   ```

Rules for the shared database:
- Write only through reducers.
- Only Rabah publishes the module to Maincloud. Send new table or reducer shapes to Rabah.
- Never publish with `--delete-data`. It wipes every table, and there is no undo.
- Every table is public, so use demo data only.

### 4. Run each agent on its own

From the repo root. Add `--no-save` to skip writing to the database.

```bash
data/.venv/bin/python agents/health/agent.py agents/health/cases/mold_basic.json --no-save
```

```bash
data/.venv/bin/python agents/legal/agent.py agents/legal/cases/mold_oakland.json --no-save
```

```bash
data/.venv/bin/python agents/property/agent.py --address "7951 MacArthur Blvd, Oakland, CA 94605" --no-save
```

```bash
data/.venv/bin/python agents/plan/agent.py --address "1510 Webster St, Oakland, CA 94612" --case-id plan_webster_1510 --no-save
```

The vision agent never calls Claude unless you pass `--live`. Without it, it only uses cached responses. `--dry-run` extracts frames and prints the estimated cost.

```bash
data/.venv/bin/python agents/vision/agent.py agents/vision/cases/jakarta_demo.json --video walkthrough.mp4 --segment 270-330 --dry-run
```

### 5. Run the pipeline through SpacetimeDB

1. Seed a demo case with 3 unconfirmed hazards:

   ```bash
   data/.venv/bin/python integration/seed_demo.py
   ```

2. Start the orchestrator. It runs health and legal on every hazard as soon as it's confirmed:

   ```bash
   data/.venv/bin/python integration/orchestrator.py --case-id case_demo_002
   ```

3. Confirm a hazard by calling the `confirm_hazard` reducer with the hazard's id, which `seed_demo.py` prints. This spends Claude credits, because the orchestrator then runs the agents.

   ```bash
   curl -s -X POST https://maincloud.spacetimedb.com/v1/database/home/call/confirm_hazard -H 'Content-Type: application/json' --data '[HAZARD_ID, true]'
   ```

Options:
- `--agents health,legal,property,plan` adds the agents that run once per address.
- `--once` exits when everything is done.

End-to-end check before pushing: it seeds a new case, confirms its hazards, runs the orchestrator, and checks that every run finished with valid output.

```bash
data/.venv/bin/python integration/check_e2e.py
```

## Tests

None of the tests call an API or need keys. Run them from the repo root. Each folder runs separately, because each agent has its own `agent.py`.

| Command | Tests |
|---|---|
| `data/.venv/bin/python -m pytest agents/legal -q` | 59 |
| `data/.venv/bin/python -m pytest agents/property -q` | 40 |
| `data/.venv/bin/python -m pytest agents/plan -q` | 10 |
| `data/.venv/bin/python -m pytest agents/health -q` | 5 |
| `data/.venv/bin/python -m pytest agents/vision/tests -q` | 79 |
| `data/.venv/bin/python -m pytest tenant/tests -q` | 63 |
| `data/.venv/bin/python agents/vision/evals.py` | 6 vision evals, replayed from fixtures for $0 |
| `cd web && npm run build` | Typecheck and build the app |
| `bash data/test_core.sh http://127.0.0.1:3000 home` | 11 checks of the database reducers (writes a `case_core_test` case) |

Health evals (`agents/health/evals.py`) and vision evals with `--live` call Claude and cost money.

## What costs money or leaves data behind

- **Calls Claude:**
  - every agent run;
  - the tenant API with `HOME_LIVE=1`;
  - `orchestrator.py` and `check_e2e.py`;
  - the vision agent with `--live`;
  - `agents/health/evals.py`.
- **Calls Apify:**
  - `data/ingest.py` without `--from-cache`;
  - the property and plan agents;
  - the legal agent with `LEGAL_RESEARCH=1`;
  - the clinics lookup (`HOME_CLINICS_LIVE=1` or `tenant.clinics_live`).
- **Sends real email:** Send in the app, once the tenant has signed in with Google and allowed Gmail access.
- **Leaves permanent rows:** there's no delete reducer. `seed_demo.py`, `check_e2e.py`, `test_core.sh`, `HOME_LIVE_SAVE=1` and any agent run without `--no-save` add rows to whichever database `STDB_URL` points at.

Measured so far (details in `docs/stack-findings.md`):
- the vision agent took a 5-minute video in 26 s for $0.152;
- the health agent takes about 9–13 s per hazard;
- the legal agent takes about 10 s per hazard in pack-only mode.

## Deploy

The live site runs on Railway as one service, built from `Dockerfile` and `railway.json`.
- The image builds `web/dist`, installs the root `requirements.txt` plus ffmpeg and poppler, and starts `python -m tenant.server`, which serves the app and the API. It binds `0.0.0.0` on Railway's `PORT`.
- Environment: `ANTHROPIC_API_KEY`, `APIFY_TOKEN`, `STDB_URL`, `GOOGLE_CLIENT_ID`, `HOME_LIVE=1`, `HOME_LIVE_SAVE=1`, `HOME_DEV_LOGIN=1`. `VITE_GOOGLE_CLIENT_ID` is a build argument.
- Every push to `main` redeploys. Cases and uploads live in memory, so a redeploy clears them.
- The orchestrator does not run in production. The API runs the agents itself when a hazard is confirmed.
- The domain is `www.goldilocks.casa`. It is listed in the Google OAuth client's JavaScript origins and redirect URIs.

## Legal coverage

- **`data/packs/ca_oakland.json`:** Oakland, CA.
- **`data/packs/ca.json`:** the California statewide pack, used for every California city without its own pack. Its legal-aid entries can be tied to cities, counties or ZIP codes, so a tenant in Davis gets Davis and Yolo County help.
- **Other places:** with no pack for the area, the legal agent reports the hazard as "not verified for your area" instead of guessing.

Everything the app shows is general information, not legal or medical advice.

## Credits

- The vision evals use a third-party apartment walkthrough from YouTube (https://www.youtube.com/watch?v=wFxmYq9wCWM). It belongs to its creator, is downloaded locally with yt-dlp, and is not stored in this repo.
- Logos in the architecture diagram belong to their owners.

## License

MIT. See [LICENSE](LICENSE). Third-party footage and logos keep their own terms.
