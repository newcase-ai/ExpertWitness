# ExpertWitness

Search for an expert witness, then view their research profile and ask questions about it.

- `frontend/`: Vite + React + Tailwind. Profiles and the "Expert intel" chat are mock data in `src/lib/mocks/`.
- `backend/`: FastAPI expert-research API (candidate search, research jobs, cited findings). See `backend/README.md`.
  Its data (SQLite file, downloads) goes to `backend/data/`, which is gitignored.

Layout, conventions and the numbered gotchas: [CLAUDE.md](CLAUDE.md).

## See a result without running the app

[docs/demo/expert-profile-demo.html](docs/demo/expert-profile-demo.html) is a one-file snapshot of the results
page for a fictional expert (all names, cases and links are made up). Download it and open it in a browser:
the tabs and pop-ups work, no app or install needed.

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
