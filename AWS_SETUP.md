# AWS Setup — Step by Step

Companion to `DEPLOYMENT_PLAN.md` (architecture/rationale) — this is the
literal click-by-click / command-by-command guide for configuring each AWS
service. Nothing here has been executed for you; work through it in order,
since later services (App Runner, Amplify) depend on things created in
earlier steps (IAM role, ECR image, RDS endpoint, secrets).

**Region used throughout:** pick one and stay consistent (e.g. `us-east-1`,
where Bedrock's `amazon.nova-micro-v1:0` is confirmed available on this
project's AWS account). Every service below must be created in the same
region.

## 0. Prerequisites

- An AWS account with permission to create IAM roles/policies, RDS, ECR,
  App Runner, Amplify, Secrets Manager, and VPC resources.
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  installed and configured (`aws configure`) with those permissions.
- Docker installed locally (already required for local dev).
- This repo pushed to a GitHub repository you control — both Amplify and
  the ECR push step reference it. (Local git repo + first commits are
  already done; run `git remote add origin <your-repo-url> && git push -u
  origin master` if you haven't yet.)

Keep a scratch note of these as you go — later steps need them:
`AWS_ACCOUNT_ID`, `RDS endpoint`, `App Runner service URL`, `Amplify app URL`.

---

## 1. IAM — App Runner's instance role (Bedrock access)

This is the role App Runner assumes at runtime so the backend can call
Bedrock **without** any static `AWS_ACCESS_KEY_ID`/`SECRET` — safer, and
`_build_model()` in `urgency_agent.py` already falls back to this
automatically when those two env vars are unset.

**Console:**
1. IAM → **Roles** → **Create role**.
2. Trusted entity type: **AWS service** → use case **App Runner** (under
   "Use case", search "App Runner", pick **App Runner - Tasks** — this is
   the instance role trust policy, distinct from the build-time "Service"
   role).
3. Skip attaching a managed policy for now → **Next** → name it
   `canvas-agent-apprunner-instance-role` → **Create role**.
4. Open the new role → **Add permissions** → **Create inline policy** →
   JSON tab → paste:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:Converse"],
         "Resource": "arn:aws:bedrock:*::foundation-model/amazon.nova-micro-v1:0"
       }
     ]
   }
   ```
5. Name it `bedrock-invoke` → **Create policy**.
6. Note the role's ARN (shown at the top of the role's page) — you'll
   attach it to the App Runner service in step 6.

---

## 2. Amazon ECR — build and push the backend image

App Runner deploys the backend from a **container image**, not by building
your Dockerfile itself (that keeps the deployed artifact identical to what
you tested locally with `docker compose`).

**Console:**
1. ECR → **Repositories** → **Create repository**.
2. Visibility: **Private**. Name: `canvas-agent-backend`. Leave the rest
   default → **Create repository**.
3. Open it → **View push commands** and follow the 4 shown commands, or
   run these from the repo root (replace `<ACCOUNT_ID>` and `<REGION>`):
   ```bash
   aws ecr get-login-password --region <REGION> | \
     docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com

   docker build -t canvas-agent-backend ./backend
   docker tag canvas-agent-backend:latest \
     <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/canvas-agent-backend:latest
   docker push <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/canvas-agent-backend:latest
   ```
4. Confirm the image appears under the repository's **Images** tab. Note
   the full image URI — you'll need it in step 6.

Re-run these 4 commands (or wire them into CI) any time backend code
changes; App Runner can auto-redeploy on a new image push (enabled in
step 6).

---

## 3. Amazon RDS — PostgreSQL

**Console:**
1. VPC → confirm you have a VPC with at least 2 private subnets across 2
   AZs (the default VPC in most accounts already has subnets in multiple
   AZs; using the default VPC is fine for this project's scale — skip
   creating a new one unless you have a reason to).
2. RDS → **Databases** → **Create database**.
   - Engine: **PostgreSQL** (version 16.x, matching the local
     `postgres:16-alpine` image).
   - Templates: **Free tier** (if eligible) or **Dev/Test**.
   - DB instance identifier: `canvas-agent-db`.
   - Master username: `canvas_agent`. Master password: generate/save one
     (you'll put it in Secrets Manager next, not here permanently).
   - Instance class: `db.t4g.micro` (or `db.t3.micro` if `t4g` isn't
     offered in your account/region).
   - Storage: 20 GB gp3, disable storage autoscaling (unnecessary at this
     scale).
   - Connectivity: **Don't** connect to an EC2 compute resource. VPC: your
     chosen VPC. **Public access: No** (App Runner reaches it privately via
     a VPC Connector — no reason to expose it to the internet).
   - VPC security group: **Create new** → name it `canvas-agent-db-sg`.
   - Additional configuration: Initial database name: `canvas_agent`.
   - Leave the rest default → **Create database**. Takes several minutes.
3. Once available, open the instance → note the **Endpoint** (hostname)
   and **Port** (5432).
4. RDS's security group (`canvas-agent-db-sg`) needs to allow inbound
   Postgres traffic **only from App Runner's VPC connector**, which you
   create in step 5. Come back and add that rule after step 5 — don't open
   port 5432 to `0.0.0.0/0`.

Your production `DATABASE_URL` (goes into Secrets Manager next):
```
postgresql+asyncpg://canvas_agent:<PASSWORD>@<ENDPOINT>:5432/canvas_agent
```
(Same driver/format as local dev — no code change. If you want to require
TLS to RDS, which is good practice, append `?ssl=require` to the URL.)

---

## 4. AWS Secrets Manager

**Console:**
1. Secrets Manager → **Store a new secret**.
2. Secret type: **Other type of secret**. Plaintext tab → paste the
   `DATABASE_URL` string from step 3 → **Next**.
3. Secret name: `canvas-agent/database-url` → **Next** → **Next** →
   **Store**.
4. Repeat for the session secret:
   - Generate one locally: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
   - Store it as `canvas-agent/session-secret`.
5. (Only if/when real Canvas OAuth is enabled) store `CANVAS_CLIENT_SECRET`
   the same way as `canvas-agent/canvas-client-secret`.
6. Note each secret's ARN (shown on its detail page) — used in step 6.

---

## 5. VPC Connector (lets App Runner reach RDS privately)

**Console:**
1. App Runner → **VPC connectors** (left sidebar) → **Create VPC connector**.
2. Name: `canvas-agent-vpc-connector`. VPC: the same one RDS is in.
   Subnets: pick the same private subnets RDS uses. Security groups:
   **Create new** → name it `canvas-agent-apprunner-sg` (leave outbound
   open, no inbound rules needed on this one) → **Create VPC connector**.
3. Back in RDS: open `canvas-agent-db-sg` (from step 3) → **Edit inbound
   rules** → **Add rule** → Type **PostgreSQL**, Source: the
   `canvas-agent-apprunner-sg` security group you just created → **Save
   rules**. This is what actually lets App Runner reach the database while
   keeping it closed to everything else.

---

## 6. AWS App Runner — the backend service

**Console:**
1. App Runner → **Create service**.
2. Source: **Container registry** → **Amazon ECR** → **Browse** and pick
   the `canvas-agent-backend` image pushed in step 2 → Deployment trigger:
   **Automatic** (redeploys whenever a new image is pushed to this tag) →
   **Next**.
3. Service settings:
   - Service name: `canvas-agent-backend`.
   - Virtual CPU/memory: 0.25 vCPU / 0.5 GB is enough to start.
   - Port: `8000`.
   - **Environment variables** — add each of these (values not from
     Secrets Manager are plain env vars; the two secret ones use the
     "Reference to Secrets Manager secret" option in the same UI):
     | Key | Value |
     |---|---|
     | `DATABASE_URL` | *(secret reference)* `canvas-agent/database-url` |
     | `SESSION_SECRET` | *(secret reference)* `canvas-agent/session-secret` |
     | `SESSION_COOKIE_SECURE` | `true` |
     | `SESSION_COOKIE_SAMESITE` | `none` |
     | `CANVAS_BASE_URL` | `https://canvas.ualberta.ca` |
     | `AWS_REGION` | *(your region, e.g.* `us-east-1`*)* |
     | `BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` |
     | `FRONTEND_ORIGIN` | *(placeholder for now, e.g.* `https://placeholder.amplifyapp.com` *— you'll fix this in step 8)* |
     - **Do not add** `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — the
       instance role from step 1 covers Bedrock access instead.
   - **Security** → Instance role: select
     `canvas-agent-apprunner-instance-role` from step 1.
   - **Networking** → **Custom VPC** → select the
     `canvas-agent-vpc-connector` from step 5 (this is what lets it reach
     RDS — don't leave it on the default "public" networking).
   - **Health check** → Protocol HTTP, Path `/health`, leave the default
     interval/timeout/thresholds.
   - **Auto scaling** → default config (min 1, max 25) is fine to start.
4. Review → **Create & deploy**. Takes a few minutes.
5. Once running, note the service's default URL
   (`https://xxxxxxxxxx.<region>.awsapprunner.com`) — this is the backend
   URL for step 7, and what you'll `curl .../health` against to verify.

---

## 7. AWS Amplify Hosting — the frontend + whitelist

**Console:**
1. Amplify → **Create new app** → **Host web app** → **GitHub** → authorize
   and pick this repository and the branch to deploy (e.g. `master`).
2. **App settings**: since this is a monorepo, Amplify should detect
   `amplify.yml` at the repo root and offer to use it; if it doesn't
   auto-detect, explicitly check **"My app is a monorepo"** (wording may
   vary by console version) and set the app root to `frontend`. The
   committed `amplify.yml` already defines the build commands
   (`npm ci` / `npm run build`) so you shouldn't need to hand-edit them.
3. **Environment variables** (App settings → Environment variables, or
   during the create-app wizard):
   | Key | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | the App Runner URL from step 6 |
   - This is a Next.js build-time variable — it gets baked into the
     JavaScript bundle at build time, so it must be set here (not as a
     runtime-only value) and any change to it requires a rebuild.
4. Save and deploy. Wait for the build to finish (Amplify shows
   Provision → Build → Deploy → Verify stages).
5. Note the app's default URL
   (`https://<branch>.<app-id>.amplifyapp.com`) — this is what you give
   students, and what you plug into App Runner's `FRONTEND_ORIGIN` next.
6. **Set the whitelist** — App settings → **Access control** → **Manage
   access** → set this branch to **Restricted - password required** →
   enter a username and password → **Save**. This puts an HTTP Basic Auth
   prompt in front of the entire site (see `DEPLOYMENT_PLAN.md` for what
   this does and doesn't cover).

---

## 8. Close the loop: cross-wire the two services

1. App Runner → `canvas-agent-backend` service → **Configuration** →
   **Environment variables** → **Edit** → set `FRONTEND_ORIGIN` to the real
   Amplify URL from step 7 (exact scheme + host, no trailing slash) →
   **Save and deploy**. This is required for CORS to accept requests from
   the real frontend.
2. Double check `SESSION_COOKIE_SECURE=true` and
   `SESSION_COOKIE_SAMESITE=none` are set (step 6) — without these the
   session cookie won't survive the cross-origin request between the
   Amplify and App Runner domains and login will silently fail (you'll see
   a successful `/auth/dev-login` response but `/auth/me` will still 401
   right after).

---

## 9. Verification checklist

1. `curl https://<app-runner-url>/health` → `{"status":"ok"}`.
2. Open the Amplify URL in a browser → the Basic Auth prompt appears →
   enter the username/password from step 7.6.
3. Past that, the app's own login page loads → log in with a Canvas PAT →
   confirm `/auth/me` succeeds (not a silent 401 — see the cookie note
   above if it does).
4. Sync → confirm courses/assignments/descriptions populate.
5. Submit one of the manual outline URLs → confirm it's fetched and stored.
6. Click **Evaluate my assignments** → confirm the agent returns 5 ranked
   assignments with reasons.
7. Open browser dev tools → Network tab → confirm no CORS errors and that
   the session cookie is present with `Secure`/`SameSite=None` flags.

---

## 10. Tearing it down (stop billing when you're done experimenting)

Delete in roughly this order to avoid dependency errors: App Runner
service → VPC connector → RDS instance (skip the final snapshot if you
don't need the data) → ECR repository → Secrets Manager secrets (schedule
immediate deletion, not the default 30-day recovery window, if you want
them gone right away) → IAM role/policy → Amplify app. None of this is
required if you're keeping the deployment running.
