# AuraXL Standalone Uptime Monitor & Status Dashboard

A lightweight, standalone website availability monitoring service and single-page dashboard built for **[https://auraxl.com](https://auraxl.com)**.

Deployed independently on [Render](https://render.com) with PostgreSQL persistent storage.

---

## Features

- **Automated Availability Probing:** Sends HTTP GET requests to `https://auraxl.com` every 5 minutes.
- **Degraded State Detection:** Accurately categorizes state into `Up`, `Degraded` (high latency >= 3000ms), and `Down` (HTTP 400+, timeout, DNS failure, connection drops).
- **Persistent Data Store:** Supports PostgreSQL for cloud production (via `DATABASE_URL`) and SQLite for local development.
- **Single-Page Live Dashboard:** Clean, responsive, dark/light balanced UI with auto-refresh every 60 seconds, uptime % tiles, latency trends, and paginated logs.
- **RESTful API:** Full API suite for status, history, statistics, and authenticated instant manual checks.
- **Zero-Dependency on Monitored Site:** 100% standalone — makes no modifications to `https://auraxl.com`.

---

## REST API Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/` | None | Serves the single-page status dashboard UI |
| `GET` | `/health` | None | Service liveness & DB connection probe (used by Render) |
| `GET` | `/api/status` | None | Returns the latest check result |
| `GET` | `/api/checks` | None | Paginated check logs (`?limit=50&page=1&status=up`) |
| `GET` | `/api/stats` | None | Calculated uptime % and min/avg/max response times (24h, 7d, 30d, all-time) |
| `POST` | `/api/check-now` | Bearer Token | Triggers an immediate ad-hoc probe |

---

## Local Setup & Development

```bash
# 1. Navigate to the project directory
cd D:\Auraxl\AuraXL-Uptime-Monitor

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env file
cp .env.example .env

# 4. Start local server
python app.py
```
Open **[http://localhost:10000](http://localhost:10000)** in your browser.

---

## Render Deployment

This project includes a native `render.yaml` blueprint:

1. Create a new repository on your GitHub account: `auraxl-uptime-monitor`.
2. Push this directory to your repository:
   ```bash
   git remote add origin https://github.com/<your-username>/auraxl-uptime-monitor.git
   git push -u origin main
   ```
3. In the Render Dashboard, click **New +** → **Blueprint** and select your repository.
4. Render will automatically provision the **Web Service** and the **Free PostgreSQL Database**.
