# Expense Tracker — Setup Guide

## What You're Setting Up

A shared expense tracker web app with:
- **Smart input**: Type "coffee 4.50 visa" and Claude AI parses it into a proper entry
- **Google Sheets backend**: Data lives in a shared Google Sheet
- **Password-protected access**: Only people with the password can use it
- **Dashboard**: Auto-updating charts and stats

---

## Prerequisites

- A Google account
- An Anthropic API key ([get one here](https://console.anthropic.com/))
- A free [Streamlit Community Cloud](https://share.streamlit.io/) account (for hosting)
- A GitHub account (Streamlit deploys from GitHub)

---

## Step 1: Google Cloud Service Account

This lets the app read/write to Google Sheets on your behalf.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable **Google Sheets API** and **Google Drive API**:
   - Go to APIs & Services → Library
   - Search "Google Sheets API" → Enable
   - Search "Google Drive API" → Enable
4. Create a Service Account:
   - Go to APIs & Services → Credentials
   - Click "Create Credentials" → "Service Account"
   - Name it something like `expense-tracker`
   - Click through (no extra permissions needed)
5. Create a key:
   - Click on the service account you just created
   - Go to "Keys" tab → "Add Key" → "Create new key"
   - Choose JSON → Download the file
6. **Save this JSON file** — you'll need the values for secrets.toml

---

## Step 2: Push Code to GitHub

1. Create a new GitHub repository (private recommended)
2. Push these files:
   ```
   expense-tracker/
   ├── app.py
   ├── requirements.txt
   └── secrets.toml.example    (for reference only, NOT the real secrets)
   ```
3. **Do NOT commit your actual secrets.toml**

---

## Step 3: Deploy on Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io/)
2. Click "New app"
3. Connect your GitHub repo
4. Set:
   - **Repository**: your repo name
   - **Branch**: main
   - **Main file path**: app.py
5. Click **Advanced settings** → **Secrets**
6. Paste your secrets in TOML format (copy from `secrets.toml.example` and fill in real values):

   ```toml
   APP_PASSWORD = "pick_a_strong_password"
   ANTHROPIC_API_KEY = "sk-ant-api03-your-key-here"
   SHEET_NAME = "Expense Tracker"

   [gcp_service_account]
   type = "service_account"
   project_id = "your-project-id"
   private_key_id = "your-key-id"
   private_key = "-----BEGIN PRIVATE KEY-----\nYOUR_KEY\n-----END PRIVATE KEY-----\n"
   client_email = "your-service-account@project.iam.gserviceaccount.com"
   client_id = "123456789"
   auth_uri = "https://accounts.google.com/o/oauth2/auth"
   token_uri = "https://oauth2.googleapis.com/token"
   auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
   client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."
   ```

7. Click **Deploy**

Your app will be live at `https://your-app-name.streamlit.app` within a minute or two.

---

## Step 4: Share With Others

1. Share the app URL with your group
2. Give them the shared password you set in `APP_PASSWORD`
3. Each person enters their name on login — this tags their expenses
4. Everyone sees all data in the dashboard

---

## Optional: View the Raw Google Sheet

The app automatically creates a Google Sheet. To access it directly:

1. Go to Google Sheets
2. Look for the sheet named in your `SHEET_NAME` secret
3. It will be owned by the service account — to see it in your personal Drive, share the service account email (`client_email`) with your own Google account

Or share the sheet with yourself:
- In the Google Cloud Console, find the service account email
- Open the sheet and share it with that email (Editor access)

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Could not connect to Google Sheets" | Check `gcp_service_account` values in secrets. Ensure Sheets + Drive APIs are enabled. |
| "Couldn't parse that" on smart input | Check `ANTHROPIC_API_KEY` is valid and has credits |
| App won't load | Check Streamlit logs (click "Manage app" in bottom-right) |
| Sheet not appearing in Drive | The sheet is owned by the service account. Share it with your personal email. |

---

## Running Locally (for development)

```bash
# Clone your repo
cd expense-tracker

# Install dependencies
pip install -r requirements.txt

# Set up secrets
mkdir -p .streamlit
cp secrets.toml.example .streamlit/secrets.toml
# Edit .streamlit/secrets.toml with your real values

# Run
streamlit run app.py
```

---

## Cost

- **Streamlit Community Cloud**: Free
- **Google Sheets API**: Free (within generous quotas)
- **Anthropic API**: ~$0.001–0.003 per expense parsed (very cheap)
  - 100 expenses/month ≈ $0.10–0.30