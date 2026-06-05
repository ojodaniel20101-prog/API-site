# Zentrix API

Movie & series download API. FastAPI backend + static frontend, deployed on Render.

## Project Structure

```
zentrix/
├── backend/
│   ├── main.py            # FastAPI app
│   └── requirements.txt
├── frontend/
│   └── index.html         # API docs site
├── render.yaml            # Render deploy config
└── .gitignore
```

## Deploy on Render

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "initial commit"
# create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/zentrix.git
git push -u origin main
```

### 2. Connect to Render

1. Go to [render.com](https://render.com) and sign in
2. Click **New** → **Blueprint**
3. Connect your GitHub repo
4. Render will read `render.yaml` and create both services automatically

### 3. Set Your API Keys (important)

After deploy, go to your `zentrix-api` service in Render dashboard:

1. Click **Environment**
2. Find `ZENTRIX_API_KEYS`
3. Set it to a comma-separated list of keys you want to give to users:
   ```
   zx-abc123def456,zx-xyz789ghi012
   ```
4. Click **Save** — service will restart automatically

> ⚠️ Never commit real API keys to Git. The `render.yaml` has `sync: false` for this variable so Render won't try to pull it from the file.

### 4. Update Frontend URL

Once your backend is deployed, Render gives it a URL like:
`https://zentrix-api.onrender.com`

The frontend `index.html` already has this URL set. If your service name differs, find and replace `zentrix-api.onrender.com` with your actual Render URL.

### 5. Your URLs

| Service | URL |
|---|---|
| Backend API | `https://zentrix-api.onrender.com` |
| API Docs | `https://zentrix-api.onrender.com/docs` |
| Frontend | `https://zentrix-frontend.onrender.com` |

## Local Development

```bash
cd backend
pip install -r requirements.txt
ZENTRIX_API_KEYS=mydevkey uvicorn main:app --reload
```

Then visit: `http://localhost:8000`

A dev key is auto-generated and printed to console if `ZENTRIX_API_KEYS` is not set.

## API Usage

```bash
curl "https://zentrix-api.onrender.com/v1/api/download?query=Inception&quality=1080" \
  -H "X-API-Key: your_key_here"
```

## Notes

- Free Render services spin down after 15 min of inactivity — first request after sleep takes ~30s
- Rate limit: 100 requests/minute per IP
- Upgrade to Render's paid plan ($7/mo) to avoid cold starts
