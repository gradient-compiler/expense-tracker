# Expense Tracker

A shared expense tracking web app powered by Streamlit, Google Sheets, and Claude AI.

## Features

- **Smart Input** -- Type expenses naturally (e.g. "coffee 4.50 visa" or "uber to airport $32 yesterday") and Claude AI parses them into structured entries
- **Manual Entry** -- Traditional form-based input as a fallback
- **Google Sheets Backend** -- All data is stored in a shared Google Sheet, accessible to everyone
- **Dashboard** -- Interactive charts showing spending by category, payment method, and daily trends
- **Expense History** -- Filterable table of all recorded expenses
- **Multi-user** -- Password-protected access with per-user tagging so everyone can see who spent what

## Tech Stack

- **[Streamlit](https://streamlit.io/)** -- Web UI and hosting
- **[Claude API](https://docs.anthropic.com/)** -- Natural language expense parsing
- **[Google Sheets API](https://developers.google.com/sheets/api)** -- Data storage via `gspread`
- **[Plotly](https://plotly.com/python/)** -- Interactive charts
- **[Pandas](https://pandas.pydata.org/)** -- Data manipulation

## Quick Start

```bash
# Clone the repo
git clone <your-repo-url>
cd expense-tracker

# Create a virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure secrets
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit .streamlit/secrets.toml with your real values

# Run the app
streamlit run app.py
```

## Configuration

The app requires a `.streamlit/secrets.toml` file with:

```toml
APP_PASSWORD = "your_shared_password"
ANTHROPIC_API_KEY = "sk-ant-..."
SHEET_NAME = "Expense Tracker"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."
```

See [docs/SETUP.md](docs/SETUP.md) for a full step-by-step setup guide covering Google Cloud, Anthropic API, and Streamlit deployment.

## Deployment

The app is designed to deploy on [Streamlit Community Cloud](https://share.streamlit.io/) (free tier). Push the repo to GitHub, connect it to Streamlit Cloud, paste your secrets into the Streamlit secrets UI, and deploy. Full instructions are in [docs/SETUP.md](docs/SETUP.md).

## Cost

| Service | Cost |
|---------|------|
| Streamlit Community Cloud | Free |
| Google Sheets API | Free (within quotas) |
| Anthropic API | ~$0.001--0.003 per parsed expense |

## License

MIT
