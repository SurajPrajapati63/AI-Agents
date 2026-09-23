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
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=sourcewise
VECTOR_STORE_DIRECTORY=/data/vector_store
CORS_ORIGINS=http://ai-agent-rag.vercel.app,https://ai-agent-rag.vercel.app,https://ai-agents-inky-two.vercel.app,http://127.0.0.1:5173,http://localhost:5173
CORS_ORIGIN_REGEX=https://ai-agents-[a-z0-9-]+-suraj-prajapatis-projects-b7c1f72a\.vercel\.app
MAX_FILE_SIZE=10485760
```

`MONGODB_URI` points to the MongoDB deployment used for user accounts and
revocable login sessions. `MONGODB_DATABASE` selects the database name. Keep
MongoDB credentials in the backend environment and never expose them to the
frontend.

For MongoDB Atlas, create a database user, copy the connection string for the
actual cluster host, and add the Render service to Atlas Network Access. For a
Render service without fixed outbound IPs, use `0.0.0.0/0` in Atlas Network
Access and rely on a strong database password. Replace any `<cluster-host>` or
other placeholder in the URI; `cluster0.xxxxx.mongodb.net` is not a valid host.
If the password contains characters such as `@`, `:`, `/`, or `#`, URL-encode
them before placing the value in `MONGODB_URI`. Set this variable in Render's
Environment settings and redeploy; Render does not read the repository's local
`.env` file.

In Render, the variable value must be only the URI, for example:

```text
mongodb+srv://<db-user>:<url-encoded-password>@<real-cluster-host>/?retryWrites=true&w=majority
```

Do not paste `MONGODB_URI=`, quotes, or the Atlas placeholder values into the
value field.

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