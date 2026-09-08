# Local dev/testing only (used by docker-compose.yml). The real deployment
# target (AWS Amplify Hosting) builds this app directly from source on
# every git push - it never uses this Dockerfile. See DEPLOYMENT_PLAN.md.
FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
