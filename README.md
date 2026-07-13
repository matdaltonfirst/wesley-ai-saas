# Wesley AI SaaS

A multi-tenant AI assistant platform built for United Methodist churches. Wesley AI helps church staff answer congregant questions, manage communications, and surface knowledge from sermons, documents, and websites — all grounded in Wesleyan theology.

## Features

- **AI Chat (Staff Dashboard)** — Conversational assistant powered by Google Gemini, with citation-backed answers drawn from each church's own knowledge base.
- **Embeddable Widget** — A branded chat widget that churches embed on their public website so visitors can ask questions 24/7.
- **Document Knowledge Base** — Upload PDFs and DOCX files; content is extracted and indexed for retrieval-augmented generation.
- **Website Crawler** — Automatically crawls and indexes a church's website so the bot can answer questions about ministries, events, and policies.
- **Sermon Ingestion** — Connects to a church's YouTube channel, transcribes new sermons, and distills summaries, main points, and scripture references.
- **Calendar Integration** — Imports iCal/ICS feeds so the assistant can answer "What's happening this week?" with live event data.
- **Communications Triage** — Staff submit communications requests (bulletins, social media, videos) and Wesley prioritises and drafts content.
- **Planning Center Integration** — OAuth connection to Planning Center Online for people and group data.
- **Weekly Activity Digest** — Automated Monday email summarising widget conversations, guest connections, and engagement stats.
- **Multi-Tenant & Team Support** — Each church is an isolated tenant; admins can invite staff members with scoped roles.
- **Billing (Stripe + Manual)** — Built-in subscription management with Stripe Checkout, trials, and manual payment tracking for churches paying by check.
- **Customisable Branding** — Per-church bot name, welcome message, colour theme, starter questions, and subtitle.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3 / Flask |
| AI | Google Gemini (gemini-2.5-flash-lite, gemini-2.5-flash) |
| Database | SQLite via Flask-SQLAlchemy |
| Auth | Flask-Login with password hashing |
| Payments | Stripe |
| Email | Resend |
| Scraping | Playwright + BeautifulSoup4 |
| Scheduling | APScheduler (background jobs) |
| Deployment | Railway (Nixpacks) / Gunicorn |

## Project Structure

```
├── app.py                 # Application factory, scheduler, middleware
├── config.py              # Shared constants and env-var configuration
├── models.py              # SQLAlchemy models (Church, User, Document, etc.)
├── helpers.py             # Utility functions (CSRF, formatting, etc.)
├── crawler.py             # Website crawling logic
├── sermons.py             # YouTube sermon ingestion & transcription
├── digest.py              # Weekly activity digest email assembly
├── comms_triage.py        # Communications request prioritisation
├── calendar_feed.py       # iCal feed parsing
├── documents.py           # PDF/DOCX extraction
├── emails.py              # Transactional email templates
├── knowledge_packs.py     # Pre-built knowledge bundles
├── pco.py                 # Planning Center API helpers
├── routes/                # Flask Blueprints
│   ├── auth.py            #   Login, signup, password reset
│   ├── chat.py            #   Staff AI chat
│   ├── widget.py          #   Public embeddable widget
│   ├── settings.py        #   Church settings & branding
│   ├── documents_routes.py#   Document upload & management
│   ├── sermons_routes.py  #   Sermon source management
│   ├── calendars.py       #   Calendar feed management
│   ├── comms_routes.py    #   Communications requests
│   ├── pco_routes.py      #   Planning Center OAuth flow
│   ├── stripe_routes.py   #   Billing & webhooks
│   ├── admin.py           #   Super-admin panel
│   └── pages.py           #   Static/marketing pages
├── templates/             # Jinja2 HTML templates
├── static/                # CSS, JS, images
├── tests/                 # Test suite
├── requirements.txt       # Python dependencies
├── Procfile               # Heroku-style process definition
└── railway.toml           # Railway deployment config
```

## Getting Started

### Prerequisites

- Python 3.11+
- A Google Gemini API key

### Installation

```bash
# Clone the repository
git clone https://github.com/matdaltonfirst/wesley-ai-saas.git
cd wesley-ai-saas

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (used for website crawling)
playwright install chromium
```

### Configuration

Copy the example environment file and fill in your keys:

```bash
cp .env.example .env
```

Required environment variables:

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Google Gemini API key |
| `SECRET_KEY` | Flask session secret (long random string) |

Optional environment variables:

| Variable | Description |
|----------|-------------|
| `DATA_DIR` | Path for SQLite database and uploads (default: `./data`) |
| `STRIPE_SECRET_KEY` | Stripe secret key for billing |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `RESEND_API_KEY` | Resend API key for transactional emails |
| `PCO_CLIENT_ID` | Planning Center OAuth client ID |
| `PCO_CLIENT_SECRET` | Planning Center OAuth client secret |
| `PCO_TOKEN_ENCRYPTION_KEY` | Encryption key for stored PCO tokens |
| `YOUTUBE_API_KEY` | YouTube Data API key for sermon ingestion |
| `APP_URL` | Public URL of the app (default: `https://app.wesleyai.co`) |
| `DEFAULT_TIMEZONE` | Fallback timezone (default: `America/New_York`) |
| `SUPER_ADMIN_EMAIL` | Email for the super-admin account |

### Running Locally

```bash
flask run --debug
```

The app will be available at `http://localhost:5000`.

## Deployment

Wesley AI is configured for [Railway](https://railway.app):

1. Connect your GitHub repo to Railway.
2. Add a persistent volume and set `DATA_DIR` to its mount path (e.g., `/app/data`).
3. Set all required environment variables in the Railway dashboard.
4. Railway will auto-detect the Nixpacks builder and use the start command from `railway.toml`.

The app can also run on any platform that supports Python + Gunicorn (Heroku, Render, VPS, etc.) using the `Procfile`.

## Testing

```bash
python -m pytest tests/
```

## License

Proprietary — All rights reserved.
