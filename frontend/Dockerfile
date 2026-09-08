# Used both for local dev (via docker-compose.yml, which overrides CMD to
# `npm run dev` for hot-reload) and as the real deployment artifact (built
# and run directly on the EC2 instance via docker-compose.prod.yml) - see
# DEPLOYMENT_PLAN.md / AWS_SETUP.md. Keep this image production-correct.
FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Next.js inlines NEXT_PUBLIC_* vars into the JS bundle at build time, not
# at container runtime - so this has to be a build ARG, not just an env var
# set later. Empty string here means "same origin, relative paths" (see
# lib/api.ts), which is what the production Caddy reverse-proxy setup
# expects; local dev overrides this via docker-compose.yml's build args.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
