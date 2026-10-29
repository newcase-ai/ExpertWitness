# ExpertWitness

Search for an expert witness, then view their research profile and ask questions about it.

- `frontend/`: Vite + React + Tailwind. Profiles and the "Expert intel" chat are mock data in `src/lib/`.
- `backend/`: FastAPI expert-research API (candidate search, research jobs, cited findings). See `backend/README.md`.

## Run

```sh
# backend (http://localhost:8000)
cd backend
uv sync
uv run fastapi dev app/main.py

# frontend (http://localhost:5173, proxies /api to the backend)
cd frontend
npm install
npm run dev
```

Try searching "Margaret Chen" or "Robert Alvarez".
