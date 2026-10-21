# The desk as one container: FastAPI serving both the API and the screens.
#
# The book lives in Neon, not in here - DATABASE_URL points at it - so the
# container keeps nothing of its own and any number of them can run at once.
# Without that variable the app falls back to SQLite on local disk, which on a
# container platform is erased at every deploy: fine to try it out, never for
# real trades.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a change to the app does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY web ./web
COPY run.py ./

# Cloud Run hands the port in; 8080 is its default for anything else.
ENV PORT=8080
EXPOSE 8080

# One worker: every write is a transaction on Neon, and the platform adds
# instances rather than threads when the desk gets busy.
CMD exec uvicorn backend.api:app --host 0.0.0.0 --port ${PORT} --workers 1
