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

Production API requests use the `/api` path. `vercel.json` rewrites that path
to the Render service, so `API_URL` does not need to be set in Vercel.

The FastAPI service must run separately with persistent vector-store storage.
Add `http://ai-agent-rag.vercel.app,https://ai-agent-rag.vercel.app,https://ai-agents-inky-two.vercel.app` to the API's `CORS_ORIGINS` value. Never put
`GROQ_API_KEY` in Vercel or in any frontend environment variable.
