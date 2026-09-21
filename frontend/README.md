# Sourcewise frontend

This is the Vite/React client for the document Q&A application.

## Local development

```powershell
npm ci
$env:VITE_API_URL="http://localhost:8000"
npm run dev
```

## Vercel deployment

Import the repository into Vercel and set the project root to `frontend`.
Vercel will use `vercel.json` and run `npm ci` followed by `npm run build`.

Set this production environment variable in Vercel:

```text
VITE_API_URL=https://your-api-service.example.com
```

The API must be deployed separately with persistent Chroma storage. Add the
Vercel deployment URL to the API's `CORS_ORIGINS` value. Never put
`GROQ_API_KEY` in Vercel or any `VITE_*` variable.# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler
The API must be deployed separately with persistent Chroma storage. Add the
Vercel deployment URL to the API's `CORS_ORIGINS` value. Never put
`GROQ_API_KEY` in Vercel or any `VITE_*` variable.
## Expanding the Oxlint configuration
