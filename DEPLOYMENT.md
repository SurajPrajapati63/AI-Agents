# Production deployment

The React client is deployable to Vercel. The FastAPI service must run
separately because Vercel serverless functions do not provide durable local
disk for the vector store.

## Backend

Deploy the repository's Python service to a host with persistent storage.

```powershell
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\uvicorn api:app --host 0.0.0.0 --port 8000
```

Set these backend environment variables:

```text
GROQ_API_KEY=your_backend_only_key
VECTOR_STORE_DIRECTORY=/data/vector_store
CORS_ORIGINS=https://your-project.vercel.app
MAX_FILE_SIZE=10485760
```

Do not expose `GROQ_API_KEY` to the browser or prefix it with `VITE_`.

## Vercel frontend

1. Import the repository into Vercel.
2. Set the Vercel project root directory to `frontend`.
3. Keep the build command as `npm run build` and output directory as `dist`.
4. Add the production environment variable:

```text
VITE_API_URL=https://your-backend.example.com
```

5. Deploy, then copy the generated Vercel URL into the backend's
   `CORS_ORIGINS` value and redeploy the backend.

The frontend uses `frontend/vercel.json`, `npm ci`, and a production Vite
build. The API URL is required in production and falls back to localhost only
during local Vite development.