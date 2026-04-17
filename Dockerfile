# -- Stage 1: Build Mini App frontend --
FROM node:20-slim AS frontend
WORKDIR /build
COPY webhook/mini-app/package.json webhook/mini-app/package-lock.json* ./
RUN npm install
COPY webhook/mini-app/ ./
RUN npm run build

# -- Stage 2: Python runtime --
FROM python:3.11-slim

WORKDIR /app

COPY webhook/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY webhook/ ./webhook/
COPY execution/ ./execution/
COPY .github/workflows/ ./.github/workflows/

# Copy built frontend from stage 1
COPY --from=frontend /build/dist ./webhook/mini-app/dist/

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "webhook.bot.main"]
