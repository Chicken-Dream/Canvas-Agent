# Running the app

Two ways to run it: **Docker (recommended, one command)** or **manual dev
servers (useful if you want hot-reload / to run tests alongside it)**. Both
end with the same result: a dashboard at `http://localhost:3000` showing
your real Canvas courses, assignments, and due dates.

## 0. Get a Canvas Personal Access Token

Real OAuth login needs a Developer Key that UAlberta IT would have to issue
- not something a student can self-serve. So for now, log in with a
Personal Access Token instead (the backend treats it identically to an
OAuth token):

1. Go to <https://canvas.ualberta.ca/profile/settings>
2. Scroll to **Approved Integrations** → click **+ New Access Token**
3. Give it any purpose name (e.g. "assignment dashboard"), leave the
   expiry blank or set one, click **Generate Token**
4. Copy the token immediately - Canvas only shows it once

You'll paste this into the login page in step 3/4 below, not into any
config file.

## 0b. (Optional) AWS Bedrock access, for the urgency agent

The "Most urgent assignments" panel on the dashboard needs an AWS IAM
user with Bedrock access. Skip this if you just want courses/assignments -
everything else works without it.

1. Get an `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for an IAM user
   with `bedrock:InvokeModel` / `bedrock:Converse` permissions.
2. Put the two keys plus `AWS_REGION` and `BEDROCK_MODEL_ID` into `.env`
   (see `.env.example` for the exact variable names). The default model,
   `amazon.nova-micro-v1:0`, is Amazon's own cheapest Bedrock model and
   needs no extra setup - Bedrock foundation models are enabled
   automatically on first use.
3. Only if you switch `BEDROCK_MODEL_ID` to an **Anthropic** model: AWS
   requires a one-time "Anthropic use case details" acknowledgment per
   account before `Converse` calls to those models succeed (Bedrock
   console → Model access). A `ResourceNotFoundException` mentioning this
   means it's not done yet - wait ~15 minutes after submitting it.

## Option A: Docker (recommended)

**Prerequisites:** Docker + Docker Compose.

```bash
cd /mnt/e/Agents_for_humans_canvas
cp .env.example .env
```

Open `.env` and set a random `SESSION_SECRET`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste the output as the value of `SESSION_SECRET` in `.env`. Leave
`CANVAS_CLIENT_ID` / `CANVAS_CLIENT_SECRET` blank - those are only needed
once UAlberta issues an OAuth Developer Key. Everything else in
`.env.example` already defaults correctly for local use.

```bash
docker compose up --build
```

Wait for all three services (`postgres`, `backend`, `frontend`) to report
ready, then jump to **Step 3** below.

## Option B: Manual dev servers (no Docker)

**Prerequisites:** Python 3.12+, Node.js 18+, and a local Postgres instance
(or see the SQLite note below).

> **Easiest way to get that Postgres instance:** if you have Docker
> available at all, let it run *just* the database container and do
> everything else manually:
> ```bash
> cd /mnt/e/Agents_for_humans_canvas
> cp .env.example .env   # if you haven't already
> docker compose up postgres -d   # or: docker-compose up postgres -d
> ```
> That exposes Postgres on `localhost:5432` with the `canvas_agent` /
> `change_me` defaults from `.env.example`, which is exactly what the
> `DATABASE_URL` below expects. If `docker compose` (space) isn't
> recognized, see the compose-plugin entry in Troubleshooting.

**Backend:**

```bash
cd /mnt/e/Agents_for_humans_canvas/backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Set the env vars the backend needs (adjust `DATABASE_URL` to a Postgres
instance you have running, or see the SQLite shortcut below):

```bash
export DATABASE_URL="postgresql+asyncpg://canvas_agent:change_me@localhost:5432/canvas_agent"
export SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FRONTEND_ORIGIN="http://localhost:3000"
```

> **No Postgres handy?** For a quick local look only (not how the app ships)
> you can point `DATABASE_URL` at SQLite instead - just also
> `pip install aiosqlite` first:
> `export DATABASE_URL="sqlite+aiosqlite:///./dev.db"`

```bash
uvicorn app.main:app --reload --port 8000
```

Leave this running. In a **second terminal**, start the frontend:

```bash
cd /mnt/e/Agents_for_humans_canvas/frontend
npm install
cp .env.local.example .env.local   # sets NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

## Step 3: Log in

Open **http://localhost:3000** in your browser. You'll be redirected to
`/login`. Paste the Personal Access Token from Step 0 into the "Personal
Access Token" field and submit.

## Step 4: Pull your courses

On the dashboard, click **Sync from Canvas**. This pulls every active
course, all their assignments/due dates/submission status, runs the
course-outline detector, and fetches each course's official UAlberta
calendar description - it takes a few seconds to a minute depending on how
many courses you're enrolled in (each one gets a few extra API calls to
hunt for its syllabus/outline plus one catalogue-page fetch).

When it finishes you'll see:
- A card per course, with an outline-confidence badge, linking to that
  course's detail page
- A table of upcoming assignments across all courses, color-coded by how
  soon they're due, each linking back to Canvas

Click into any course card to see its full assignment list, set a
subjective difficulty rating (1-5), see its official course description
(with a link to the UAlberta catalogue page), and see exactly where/how its
outline was found - or if the auto-detector got it wrong (see Step 4b) or
found nothing.

## Step 4b: (Optional) Point it at the right outline yourself

The auto-detector is a heuristic and sometimes lands on the wrong link (or
none at all). On the course detail page, paste the actual outline URL into
the box under "Course outline" and click **Submit** - it works whether
that's a Canvas page, a Canvas file (PDF included), or an external site
like a GitHub Pages syllabus. It's fetched once immediately; re-syncing
afterward won't overwrite it. Pasting a new link later replaces the old one.

## Step 5: (Optional) Evaluate your most urgent assignments

If you set up AWS Bedrock access in step 0b, click **Evaluate my
assignments** in the panel just above the course grid. This runs a Strands
agent that reads your deadlines, course outlines, and course descriptions,
and returns your 5 most urgent assignments with a one-sentence reason each.
It only runs when you click the button - the result is saved and shown on
future visits until you click it again. A run typically takes several
seconds to tens of seconds depending on how many courses/tool calls it needs.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Login page keeps redirecting back to itself | Backend isn't reachable at the URL in `NEXT_PUBLIC_API_URL` (frontend) - check the backend terminal/logs. |
| `401 Canvas rejected this access token` | Token was mistyped, expired, or revoked - generate a new one (Step 0). |
| Sync is very slow | Normal for many courses - outline detection makes several extra API calls per course. Only an issue if it never completes; check the backend logs for errors. |
| CORS error in the browser console | `FRONTEND_ORIGIN` (backend) doesn't match the URL you're actually loading the frontend from. |
| `docker compose`: "compose is not recognized" / "unknown command" | The Compose v2 plugin isn't installed alongside your Docker Engine (common on a manual WSL Docker install). Run `sudo apt-get install docker-compose-plugin`, or if you're on Docker Desktop, enable WSL integration for your distro in Docker Desktop → Settings → Resources → WSL Integration. The old hyphenated `docker-compose` binary works as a fallback if you have it. |
| Backend crashes on startup with `ConnectionRefusedError: [Errno 111]` from asyncpg | Nothing is listening on the host/port in `DATABASE_URL` - i.e. Postgres isn't running. This only happens with manual startup (Option B), since Docker Compose normally starts the `postgres` container for you. Start it yourself: `docker compose up postgres -d` (see the note in Option B), or install/start Postgres natively. |
| "Evaluate my assignments" fails with `Model use case details have not been submitted for this account` | AWS-side, one-time per account - see step 0b. Submit the Anthropic use case form on the Bedrock → Model access page in the AWS Console, then wait ~15 minutes and retry. |
| "Evaluate my assignments" fails with `AWS Bedrock credentials are not configured` | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` aren't set in `.env` (or the backend wasn't restarted after adding them) - see step 0b. |
