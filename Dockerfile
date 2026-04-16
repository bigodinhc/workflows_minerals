FROM python:3.11-slim

WORKDIR /app

COPY webhook/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY webhook/ ./webhook/
COPY execution/ ./execution/
COPY .github/workflows/ ./.github/workflows/

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "webhook.bot.main"]
