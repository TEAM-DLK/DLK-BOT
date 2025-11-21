# DLK Bot — Deployment helper (short notes)

This repository contains deployment helper files for these hosts:
- Heroku (Procfile + app.json)
- Replit (.replit + start.sh)
- Docker (Dockerfile + docker-compose.yml)
- Koyeb (koyeb.yml using Dockerfile)
- Railway (Dockerfile provided as railway.Dockerfile)
- VPS (systemd service example dlk_radio.service)

Quick steps:

1) Local / VPS
   - Copy .env.example -> .env and fill secrets.
   - Install deps: pip install -r requirements.txt
   - Run: python main.py
   - For systemd: place files at /opt/dlk, create user dlk, set EnvironmentFile path, then:
     sudo systemctl daemon-reload
     sudo systemctl enable dlk_radio
     sudo systemctl start dlk_radio

2) Docker / Docker Compose
   - Build: docker-compose build
   - Up: docker-compose up -d

3) Heroku
   - Push repository.
   - Set config vars from .env.
   - Use worker process (Procfile) — Heroku will run the worker.

4) Replit
   - Add the repo to Replit.
   - Set secrets in Replit UI (do NOT commit secrets).
   - .replit runs start.sh which installs deps and runs main.py.

5) Koyeb / Railway
   - Use the Dockerfile and point the service to build from this repo (or use their GitHub integration).
   - Add environment variables in the platform UI.

Important:
- Do NOT commit .env with real credentials.
- Some platforms (Heroku, Replit secret UI, Koyeb, Railway) provide secret stores — use them instead.
- For persistent storage (download caches, thumbnails), prefer mounting a volume or external storage.
