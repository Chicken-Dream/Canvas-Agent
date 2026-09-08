# Canvas Assignment Agent — Prototype

Pulls courses, assignments, submission deadlines, and (best-effort) course
outlines from a student's Canvas account at **canvas.ualberta.ca**, and
displays them in a web dashboard.

Includes a **Strands Agents SDK** agent (running on AWS Bedrock) that ranks
your assignments by urgency — deadline × subjective course difficulty ×
assignment weighting from the outline × how demanding the course itself is —
and explains its reasoning per assignment. Future work: mobile notifications
and multi-student OAuth login (see "Designed-in hooks" below).

## Stack

| Layer    | Choice                                   | Why |
|----------|-------------------------------------------|-----|
| Backend  | FastAPI (Python, async)                   | Same language as the planned Strands agent — it can import this backend's DB models directly later. |
| Database | PostgreSQL + SQLAlchemy (async)           | Relational fit for courses → assignments → outlines; easy for an agent to query later. |
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind | Standard, fast to iterate on. |
| Canvas access | Canvas LMS REST API, OAuth2 **and** Personal Access Token | See "Auth" below — UAlberta OAuth needs an approved Developer Key. |
| Urgency agent | Strands Agents SDK + AWS Bedrock (Claude) | See "Urgency agent" below. |

## Project layout

```
backend/
  app/
    main.py            FastAPI app, CORS, startup
    config.py           env-driven settings
    models.py            User, CanvasCredential, Course, Assignment, CourseOutline
    schemas.py            Pydantic response/request models
    deps.py               auth dependency + per-user Canvas client
    security.py            signed session cookies + OAuth state
    catalogue.py            UAlberta course-calendar description scraper
    canvas/
      client.py            Canvas REST API wrapper (pagination, OAuth exchange)
      outline.py            heuristic detector + manual-URL outline resolver
      sync.py                pulls Canvas -> upserts DB
    agent/
      tools.py               assignment/outline/catalogue tools the agent calls
      urgency_agent.py         Strands Agent + BedrockModel wiring, structured output
    routers/
      auth.py, courses.py, assignments.py, sync.py, agent.py
frontend/
  app/
    page.tsx               dashboard (urgency panel + courses + upcoming assignments)
    login/page.tsx           OAuth button + PAT fallback form
    courses/[id]/page.tsx     course detail: outline submission + description + assignment list
  lib/api.ts                 typed fetch client
docker-compose.yml
```

## Running it

```bash
cp .env.example .env        # then edit values, see "Auth" below
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend:  http://localhost:8000 (interactive docs at `/docs`)
- Postgres: localhost:5432

The backend creates its tables on startup (no migrations yet — see
"Known limitations").

## Auth: OAuth vs. Personal Access Token

Canvas OAuth2 on an **institutional** instance (`canvas.ualberta.ca`) requires
a **Developer Key** issued by the school's Canvas admins (UAlberta IT) —
students cannot self-register one. The full OAuth2 flow is implemented
(`GET /auth/canvas/login` → Canvas consent screen → `GET
/auth/canvas/callback`) and is what should be used once such a key is
approved and `CANVAS_CLIENT_ID` / `CANVAS_CLIENT_SECRET` are set in `.env`.

**Until then**, this prototype ships a second, fully-functional login path:
a student generates a **Personal Access Token** themselves at
`canvas.ualberta.ca → Account → Settings → New Access Token`, and pastes it
into the login page. The backend exchanges it for the student's profile
(`GET /api/v1/users/self`) exactly the way it would with an OAuth token —
both are just bearer tokens as far as the Canvas API client is concerned, so
the rest of the app (sync, outline detection, dashboard) doesn't need to
know which one it's using.

This is why `CanvasCredential.token_type` is `"oauth" | "pat"`: swapping to
real OAuth once a developer key exists is a config change, not a rewrite.

## What "find the course outline" actually does

Canvas has no dedicated "outline" object — UAlberta instructors surface it
inconsistently. `backend/app/canvas/outline.py` searches, in order, and
keeps the highest-confidence hit:

1. The course's **Syllabus** tab body (`syllabus_body`) — if it contains a
   link to an external domain (e.g. a GitHub Pages site), that link wins.
2. **Modules** items titled like "Course Outline" / "Syllabus" — handles the
   common case of an `ExternalUrl` module item that redirects straight off
   Canvas, or a `Page` item that itself contains the external link.
3. The course's **Pages** list, for any page titled with those keywords.

If an external destination is found, the backend also makes a best-effort
fetch of that page's text (stored in `CourseOutline.fetched_content`) — this
is the field the future weighting agent will read to extract things like
"Assignment 2 — 15%".

Each course's dashboard/detail view shows what was found, its source, a
confidence score, and links to both the Canvas item and the external page —
plus a manual link to Canvas when nothing was found, since the heuristic
will not catch every layout.

**Manual override — submit your own link:** the heuristic above sometimes
lands on a mislabeled or wrong link (see "Known limitations"). On the course
detail page, paste the actual outline URL and submit it
(`POST /api/courses/{id}/outline`) — it's fetched and stored immediately
(source `"user_submitted"`, confidence 1.0), the right way for whatever kind
of link it is: a Canvas Page via the authenticated Pages API, a Canvas file
via the Files API + `pypdf` text extraction (handles PDFs), or a plain fetch
for an external site (GitHub Pages, etc). This is a one-time fetch, not a
standing config: syncing again never touches or re-scrapes a course whose
outline came from `"user_submitted"` - submit again (it replaces the old
one) if the link changes.

## Course description

Each course's detail page also shows its official UAlberta course-calendar
description (e.g. `apps.ualberta.ca/catalogue/course/cmput/379`), with a
link to the catalogue page itself. Unlike the outline, this needs no user
action - `app/catalogue.py` scrapes it automatically during sync and caches
it on `Course.description_text` / `description_url` (fetched once, like the
outline; re-syncing won't refetch it once set). The Strands agent's
`get_course_description` tool (below) hits the same page live rather than
reading this cached copy, since the agent may want it for a course context
beyond what's cached.

## Urgency agent (Strands Agents SDK + AWS Bedrock)

The dashboard's "Most urgent assignments" panel (between the welcome header
and the course grid) is a Strands `Agent` running on AWS Bedrock. Clicking
**Evaluate my assignments** (`POST /api/agent/urgency/evaluate`) is the only
thing that runs it — it does not run automatically on page load or on a
schedule. The result is stored in the `agent_urgency_reports` table (one row
per user, overwritten on each re-run) and served back by
`GET /api/agent/urgency` on every subsequent load, so the panel always shows
whatever was last computed until the button is pressed again.

The agent (`backend/app/agent/urgency_agent.py`) has three tools
(`backend/app/agent/tools.py`), each a closure bound to the requesting
user's DB session so the model can't see or query anyone else's data:

- **`get_upcoming_assignments`** — the standalone "what's due soon" helper:
  every assignment due in the next 14 days, as JSON, in reverse
  chronological order (latest due date first). Also logs the same payload
  at INFO level (`app.agent.tools` logger) for later auditing. This is
  useful on its own, independent of the agent - it doesn't rank or judge
  anything, just gives a clean machine-readable deadline snapshot.
- **`get_courses`** — each course's subjective difficulty rating and
  whatever outline text was resolved for it (grading weights, policies).
- **`get_course_description(subject, number)`** — scrapes the official
  UAlberta calendar page (e.g. `apps.ualberta.ca/catalogue/course/cmput/379`)
  for a course's description, so the agent can factor in how conceptually
  demanding the subject matter is.

The system prompt asks the agent to weigh deadline proximity, course
difficulty, outline-derived grading weight, and subject demand, then return
the 5 most urgent assignments (fewer if less than 5 are due soon) each with
one sentence explaining why it made the list — via `structured_output_model`
(a Pydantic schema), not manual JSON parsing.

**AWS setup:** put an IAM user's `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
with Bedrock invoke access into `.env`, along with `AWS_REGION` and
`BEDROCK_MODEL_ID`. Without these set, the evaluate endpoint returns a `503`
explaining what's missing rather than crashing.

**Model choice:** defaults to `amazon.nova-micro-v1:0` - Amazon's own
cheapest Bedrock model, deliberately picked over Anthropic's Bedrock models.
The Anthropic ones require a one-time "Anthropic use case details"
acknowledgment per AWS account before `Converse` calls succeed (a
`ResourceNotFoundException` mentioning this means it hasn't been submitted
yet, in the Bedrock console's Model access page); Nova Micro has no such
gate and, verified against this app's real tools/structured-output pipeline,
correctly reads and cites deadlines, course difficulty, and outline/weight
data in its reasoning. Point `BEDROCK_MODEL_ID` at any other Bedrock model
(e.g. `amazon.nova-lite-v1:0` for more capable reasoning, or an Anthropic
model once the use-case form is submitted) if you want to trade cost for
quality.

## Designed-in hooks for the remaining planned features

- **Mobile notifications:** assignment sync already normalizes `due_at` to
  UTC and tracks `submission_status`; a notifier just needs to diff against
  what it last sent.
- **Multi-student OAuth login:** `User`/`CanvasCredential` are already keyed
  by `canvas_user_id`, so multiple students can use the same deployment once
  real OAuth is enabled — no PAT is stored beyond what a login flow would
  otherwise get from OAuth.

## Running the backend tests

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -v
```

80 tests, fully offline and deterministic (no real Canvas or Bedrock calls,
no Postgres required) - they run against an in-memory SQLite DB with
`CanvasClient`'s network methods monkeypatched, using `httpx.ASGITransport`
to drive the real FastAPI app + dependency chain, and a fake `strands.Agent`
for the urgency agent's tool-wiring/error-handling logic:

- `tests/test_outline.py` - the outline-detection heuristic against every
  source it checks (syllabus body, module items, pages list), the
  confidence-ordering/early-exit logic, network-failure handling, and
  `resolve_outline_from_url` (the manual-submission resolver: Canvas page,
  PDF-via-Files-API, external site).
- `tests/test_catalogue.py` - subject/number parsing and the course
  description scraper (real HTML structure, fetch failure, layout fallback).
- `tests/test_sync.py` - Canvas -> DB upsert logic, that re-running a sync
  updates existing rows instead of duplicating them, that a `user_submitted`
  outline is never re-detected on sync, and that the course description is
  fetched once and cached rather than refetched every sync.
- `tests/test_security.py` - session cookie / OAuth state signing, tampering,
  and expiry.
- `tests/test_api.py` - the HTTP layer: auth flow (dev-login, logout, bad
  token), 401/404s, per-user data scoping, difficulty validation, assignment
  filters, a full `/api/sync` round trip, and `POST /api/courses/{id}/outline`
  (fetch-and-store, replacing an existing outline, ownership checks).
- `tests/test_agent_tools.py` - the assignment-window JSON tool (window
  filtering, reverse-chronological ordering, logging) and the courses-context
  tool.
- `tests/test_urgency_agent.py` - the agent wires up its 3 tools and calls
  `invoke_async` with the right `structured_output_model`; raises a clear
  error when AWS credentials are missing or the model returns no structured
  output; and the Bedrock transient-error retry policy (see "Known
  limitations" - retries `ModelErrorException`/throttling/5xx, not
  access-denied/not-found errors).
- `tests/test_agent_api.py` - `GET/POST /api/agent/urgency`: null before
  first run, stored-and-returned after, overwrites rather than accumulates,
  and turns agent failures into clean 502/503s instead of crashing.

This suite was validated against a real UAlberta account before being
written (real OAuth-token-free login via PAT, a real `/api/sync` pulling 5
courses / 67 assignments, and outline detection actually following an
external redirect) - see git history for that session's notes. The heuristic
isn't perfect even on real data: for one real course it followed a Canvas
module item literally titled "Course Outline" to what turned out to be the
instructor's personal homepage rather than a syllabus page. That's a
correctly-executed heuristic hitting a mislabeled link, not a bug - it's
exactly the class of case flagged in "Known limitations" below.

## Known limitations (it's a prototype)

- No Alembic migrations — schema changes require dropping the dev DB.
- OAuth access-token refresh is not implemented (`refresh_token` is stored
  but unused); PATs don't expire so this doesn't block the fallback path.
- Outline detection is heuristic, not guaranteed — courses with unusual
  layouts may need manual review (the UI always shows a Canvas link as a
  fallback).
- Single Canvas instance (`CANVAS_BASE_URL`, defaults to
  `https://canvas.ualberta.ca`) per deployment.
- `frontend`'s nested `postcss` (pulled in transitively by Next 14.2.x) has
  an open advisory for a dev-server-only path-disclosure issue; fixing it
  requires the Next 16 major upgrade, deferred for this prototype.
- Manually-submitted outlines are fetched once at submission time - if the
  student's outline page/file changes later on the source site, the app
  won't notice until they re-submit the (possibly same) link.
- Bedrock intermittently returns `ModelErrorException` (a transient
  upstream-model-side 424, not an access/config issue - the identical
  request succeeds on retry) on `amazon.nova-micro-v1:0`. `run_urgency_agent`
  now retries that and a few other known-transient Bedrock error codes
  (`ThrottlingException`, `ServiceUnavailableException`,
  `InternalServerException`, `ModelTimeoutException`) via a custom
  `ModelRetryStrategy`, since Strands' default only retries its own
  `ModelThrottledException`. Non-transient errors (bad credentials, missing
  use-case acknowledgment) are never retried.
- PDF text extraction (`pypdf`) is best-effort: scanned/image-only PDFs or
  unusual encodings may yield little or no usable text for the agent.
- The urgency agent runs on `amazon.nova-micro-v1:0` by default (cheap, no
  Anthropic-specific access gate - see "Urgency agent" above), a small
  model. It correctly reads and cites deadlines/difficulty/outline data in
  its reasoning (verified against real data), but a larger model
  (`amazon.nova-lite-v1:0`/`-pro-v1:0`, or an Anthropic model once its
  one-time Bedrock use-case form is submitted) will reason more deeply
  about tradeoffs across many assignments at once.
- If you swap `BEDROCK_MODEL_ID` to an Anthropic model and see
  `ResourceNotFoundException: Model use case details have not been
  submitted for this account`, that's a one-time AWS account
  acknowledgment (Bedrock console → Model access), not an app bug.
