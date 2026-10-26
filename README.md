# ExpertWitness

Search for an expert witness, then view their research profile and ask questions about it.

- `frontend/`: Vite + React + Tailwind. Profiles and the "Expert intel" chat are mock data in `src/lib/`.
- `backend/`: FastAPI. `POST /api/search {name, hint}` returns mock candidates (`backend/main.py`).

## Run

```sh
# backend (http://localhost:8000)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
fastapi dev main.py

# frontend (http://localhost:5173, proxies /api to the backend)
cd frontend
npm install
npm run dev
```

Try searching "Margaret Chen" or "Robert Alvarez".
