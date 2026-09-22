# Sourcewise frontend

Vite/React client for the document Q&A API.

## Local development

```powershell
npm ci
$env:API_URL="http://localhost:8000"
npm run dev
```

## Vercel deployment

Set the Vercel project root directory to `frontend`. Vercel uses
`frontend/vercel.json`, installs with `npm ci`, and builds with `npm run build`.

Set this production environment variable in Vercel:

```text
API_URL=https://your-api-service.example.com
```

The FastAPI service must run separately with persistent vector-store storage.
Add the Vercel deployment URL to the API's `CORS_ORIGINS` value. Never put
`GROQ_API_KEY` in Vercel or in any frontend environment variable.
