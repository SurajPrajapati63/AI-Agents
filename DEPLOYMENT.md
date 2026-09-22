# Production deployment

The React client is deployable to Vercel. The FastAPI service must run
separately because Vercel serverless functions do not provide durable local
disk for the vector store.

## Backend

Deploy the repository's Python service to a host with persistent storage.

```powershell
pip install -r requirements.txt
uvicorn api:app --host 0.0.0.0 --port $PORT
```

Render build command:

```text
pip install -r requirements.txt
```

Render start command:

```text
uvicorn api:app --host 0.0.0.0 --port $PORT
```

Set these backend environment variables:

```text
GROQ_API_KEY=your_groq_key
GROQ_MODEL=llama-3.3-70b-versatile
VECTOR_STORE_DIRECTORY=/data/vector_store
CORS_ORIGINS=http://ai-agent-rag.vercel.app,https://ai-agent-rag.vercel.app,https://ai-agents-inky-two.vercel.app
MAX_FILE_SIZE=10485760
```

`GROQ_API_KEY` is used for answer generation. Document retrieval uses local
feature-hash embeddings, so no second embedding API key is required. Do not
expose `GROQ_API_KEY` to the browser or prefix it with `VITE_`.

## Vercel frontend

1. Import the repository into Vercel.
2. Set the Vercel project root directory to `frontend`.
3. Keep the build command as `npm run build` and output directory as `dist`.
4. Add the production environment variable:

```text
API_URL=https://ai-agents-qigt.onrender.com
```

5. Deploy, then ensure `http://ai-agent-rag.vercel.app,https://ai-agent-rag.vercel.app,https://ai-agents-inky-two.vercel.app` is included in the
   backend's `CORS_ORIGINS` value and redeploy the backend.

The frontend uses `frontend/vercel.json`, `npm ci`, and a production Vite
build. The API URL uses `API_URL` when provided and otherwise falls back to
the deployed Render service in production or localhost during development.