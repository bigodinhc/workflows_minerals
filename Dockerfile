FROM python:3.10-slim

WORKDIR /app

# Copy webhook files only
COPY webhook/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY webhook/ .

# Railway provides PORT env var
ENV PORT=8080
EXPOSE 8080

CMD gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120

