# ExpertWitness: notes for agents and teammates

Search for an expert witness, then research them into a cited profile. Two apps:
`backend/` (FastAPI, Python 3.12+, uv) and `frontend/` (Vite, React 19, TypeScript, Tailwind).

## Commands

```sh
# backend
cd backend && uv sync
uv run fastapi dev app/main.py      # API on :8000, docs at /docs
uv run pytest -q                    # all tests, offline
uv run ruff check                   # lint (line length 100, import order)

# frontend
cd frontend && npm install
npm run dev                         # :5173, proxies /api to the backend
npm run build                       # type check (tsc -b) + build
```

## Layout

```
backend/
  app/main.py              FastAPI routes only: validate, call store/jobs
  app/config.py            settings from .env / environment; DATA_DIR = backend/data
  app/models.py            Pydantic request/response shapes
  app/store.py             SQLite (stdlib sqlite3): owns every table
  app/jobs.py              background jobs (asyncio task in the same process)
  app/candidate_agent.py   candidate finder (step 1: name in, candidates out)
  app/research_agent.py    main research agent (step key stays "main_agent")
  app/llm.py               the only door to the model API
  app/http.py              shared httpx client, rate limits, URL cache
  app/evidence.py, memory.py, profile.py, trace.py
  app/sources/<name>.py    raw source clients: HTTP, rate limits, cache. No agent logic.
  app/tools/<name>.py      agent tools; each wraps a source. Registry + wrapper in tools/__init__.py
  tests/                   test_source_<name>.py, test_tool_<name>.py, test_api_<area>.py,
                           otherwise test_<module>.py; fakes.py + conftest.py hold the fakes
  data/                    SQLite file, downloads, LEIE CSV (gitignored, never commit)
  DESIGN.md                the design; section 9 is the module map
frontend/src/
  main.tsx                 routes
  pages/<Name>Page.tsx     one routed page per file
  components/AppHeader.tsx shared by every page
  components/<area>/       pieces of one page/area (profile/, run/): one component per PascalCase
                           file; a file of several small components or helpers is lowercase
                           (profile/common.tsx, profile/tabs.ts)
  components/ui/           shadcn-style primitives (kebab-case)
  lib/api.ts               the only place that calls the backend
  lib/types.ts             shared types
  lib/mocks/               mock data only (no types, no logic)
  lib/*.ts                 helpers (kebab-case)
```

## Rules for writing code here

- Don't change behavior while tidying. A rename or move is its own commit, kept separate from logic changes.
- Put code where the layout says: a new data source gets a client in `app/sources/` and a tool in `app/tools/`.
  A new page goes in `pages/` as `<Name>Page.tsx`, and its pieces go in `components/<area>/`.
- Use the standard library and what's already installed. Don't add a dependency without asking the team.
- Keep files small and focused. If a page grows past a few hundred lines, move pieces into `components/<area>/`.
- Frontend imports use the `@/` alias, not `../`.
- Tests never call live sites or the live AI (gotcha 9). Use the fakes in `tests/fakes.py`. Test data is synthetic people only (gotcha 8).
- Every SQLite file and download lives in `backend/data/` (`config.DATA_DIR`), via `store.connect()` or next to
  `DATABASE_PATH`. Never create a database anywhere else, and never commit one.
- Never commit `.env`, keys, real API responses about real people, or court documents.
- Run `uv run pytest -q`, `uv run ruff check` and `npm run build` before you push.
- `PLAN.md` and `SOURCES_AND_FLOW.md` in `backend/` are private team notes (gitignored). Don't link to them from tracked files.

## Gotchas

Code comments say "gotcha N"; N is the number below. Numbered, append only, one line each.
Copied from the private `backend/PLAN.md` §8 on 2026-11-02. File names were updated after the cleanup renames.

1. Firecrawl terms (rev. 2026-10-07) ban "hard background check" and FCRA uses; do not use it.
2. Never automate LinkedIn, Google Scholar, Daubert Tracker, TrialSmith, CPAverify, BrokerCheck, ABMS, DocInfo.
3. FCRA: never call output a background check or consumer report; professional records only.
4. CFAA: no logins to scrape, no CAPTCHA bypass, no IP rotation, no fake accounts.
5. CourtListener: 5/min, 50/hr, 125/day per account; only one account per project. Cache results.
6. OpenAlex without a key: 10 req/s and cursor paging is not allowed; read first pages only.
7. Crossref: send `mailto` for polite pool (10/s single, 3/s lists); SEC: declared User-Agent, 10 req/s. Contact email comes from `.env`, never hard-coded.
8. Public repo: never commit `.env`, the SQLite file, real API responses about real people, or court documents. Test fixtures are synthetic.
9. Tests never call live sites or the live AI; they use fakes/fixtures.
10. The AI must only summarize collected sources; a claim without a matching source is dropped.
11. CourtListener v4 returns 401 without the token even for search and opinion reads; no token = testimony is a gap, not an error.
12. OpenAlex keyless budget ($0.10/day, about 100 author searches) is shared by everyone on the same IP; one person is often split over several author IDs, so merge them.
13. Court opinions often name experts by surname only ("Dr. Loftus"); never match on surname alone, and lower the identity confidence.
14. Meta `web_search` works only on the Responses API, which uses `text.format`, not `response_format` (the other endpoint's parameter does not configure structured output); web search + JSON schema in one call is undocumented, so use two calls.
15. Meta web search snippets are third-party page text: store only the URL, title and our own extracted facts; drop results from gotcha 2 domains.
16. ruff 0.17 flags `except Exception` (BLE001) by default; a deliberate catch-all needs `# noqa: BLE001` with a reason. The project is not installed, so pytest relies on `pythonpath = ["."]`.
17. `app.http.limiters` are process-wide: a second CourtListener call in the same test run sleeps 12 s; tests must clear them (the `sources` fixture in `tests/fakes.py` does).
18. CourtListener phrase and proximity queries (`"a b"~N`) are exact: no stemming, so list plural/verb forms (expert, experts) separately; word order inside a proximity phrase is not verified.
19. TestClient runs the app's event loop in another thread: to hold a background job at "running" in a test, poll a `threading.Event` with `await asyncio.sleep`, never block on it (see `tests/test_api_research.py`).
20. `app.candidate_agent` imports `app.sources.websearch`, so websearch can only import from `app.candidate_agent` (e.g. `copied`) inside a function, never at module top (circular import).
21. ruff's BLE001 does not fire on an `except Exception` that re-raises; a `# noqa: BLE001` there is itself an error (RUF100).
22. Meta `web_search` results usually have an empty snippet: a fact step that reads only results sees titles and URLs; feed it the search call's own reply text (asked to describe pages by URL), and copy-check quoted text in it.
23. CourtListener search `absolute_url` (`/opinion/<n>/`) holds the case (cluster) id, not an opinion id; read opinion text by `opinions[].id` (cause of the empty Taylor v. Moore passages).
24. Firecrawl `proxy` defaults to `auto`, which can switch to enhanced proxies to get past bot blocks; send `proxy: basic` (gotcha 4). PDFs cost +1 credit per page, so cap `parsers[].maxPages`.
25. Firecrawl can answer 200 when the page itself failed: check `data.metadata.statusCode`, and re-check `metadata.url` (after redirects) against gotcha 2.
26. "court" in a host also matches courtlistener.com (and news sites such as courthousenews.com): the cross-check's court-site rule excludes CourtListener; the pasted `testimony_list` lives only in the job's memory, so a job interrupted by a restart cannot be re-run with it.
27. litellm 1.106.6 `openai/responses/<model>` (the Responses route) always imports its MCP gateway, so it fails with "No module named 'mcp'" (as APIConnectionError) unless `mcp` is installed; the `openai/<model>` chat route does not need it (wrong for tool calls: see gotcha 42).
28. ADK LiteLlm drops Meta `web_search_call` results and `url_citation` annotations (it maps only Gemini grounding); they are visible only to a global LiteLLM success callback, so keep web search as our own httpx FunctionTool.
29. ADK raises `LlmCallsLimitExceededError` when `RunConfig.max_llm_calls` is hit (default 500, env `ADK_MAX_LLM_CALLS`) and logs a full traceback; LiteLLM exceptions (AuthenticationError 401, RateLimitError 429) carry provider text, so wrap them into fixed-text ModelError.
30. Set `LITELLM_LOCAL_MODEL_COST_MAP=True` before the first `import litellm` (top of `app/llm.py`), as the spike does, so import does not download the cost map (network in tests).
31. litellm in its default DEV mode loads `.env` into `os.environ` at import (leaks real keys into tests); `app/llm.py` sets `LITELLM_MODE=PRODUCTION` first (ADK does so only when it imports litellm itself).
32. ADK treats a string `instruction` as a template (`{word}` is filled from session state, unknown words fail); pass a callable, as `llm.make_agent` does.
33. ADK logs every failure with a traceback from several loggers (`runners`, `workflow._node_runner`); `app/llm.py` strips the exception from every `google_adk*` log record (provider text, gotcha 29).
34. ADK puts tool parameters in `parameters_json_schema`, not `parameters`; a `functools.wraps` wrapper (`Run.tool`) keeps the tool's name, docstring and arguments.
35. LiteLLM's OpenAI client already retries 429/5xx/network errors (`max_retries`, set to 2 in `make_model`); never add our own retry around `run_agent`: it would replay tool calls.
36. `tests.fakes.call(name, **args)` takes the tool name as `name`, so no tool may have an argument called `name` (TypeError in tests); candidate tools use `full_name`.
37. `Run.tool` counts a call against the tool's cap before the tool's own checks run: a query refused for lacking the full name still uses up one call.
38. An ADK tool's name is its Python function name: a tool closure named like a module function shadows it, so the research page reader is `read_one_page` and the loop tool is `read_page`.
39. The research loop's `find_case` takes only a case name some finding already names (court and year come from that finding, never the model): an invented name would become an uncited "not_found" case_check.
40. Research findings hold CourtListener opinion and `?q=` search links: the loop's `read_page` must refuse API-source hosts (`collectors.API_HOSTS`), or Firecrawl scrapes CourtListener past its token and limits (gotchas 5, 11).
41. Case names match only exactly after `case_key` (never prefix or substring): "Doe v. Smith" is not "Doe v. Smith Industries Holdings"; a case seen only in a web fact must be read with `read_page` first.
42. litellm 1.106.6 `completion()` imports its MCP gateway for ANY call with `tools`, chat route too (fails without `mcp`, as APIConnectionError), unless the private kwarg `_skip_mcp_handler=True` is passed; `make_model` passes it (re-check on a litellm upgrade).
43. PLAN.md is private (gitignored): other engineers see only committed files, so the framework guide and tool cards live in DESIGN.md.
44. Tool names are ADK function names (gotcha 38): every tool function in `app/tools/` must have a unique name across the whole folder.
45. Memory text shown to the model never holds a secret (passwords, card numbers, tokens), whatever tool 3 decides about logins.
46. `ctx.add_finding` returns None (nothing saved) for a gotcha 2 or non-http URL, and `ctx.save_file` raises ValueError for any URL `ctx.fetch_ok` refuses: check `fetch_ok` first and drop None refs.
47. Activity rows store the first 100 chars of every string argument the model passes: no tool may take page, snippet or file text as an argument (gotcha 15).
48. `Finding.confidence` is only `high`/`low`: tool 1's `medium` level lives in `facts.match` and is saved as `low`; `find_case` passages are search snippets, so `court_case` returns them but never stores them (gotcha 15).
49. `ctx.add_finding` merges the same kind + URL into one finding: every LEIE row links the same OIG search page, so tool 11 adds `#possible-match-<n>` to keep each possible match its own finding.
50. `no_secret`'s card check (13-19 digits, dashes allowed, Luhn) also hit ~1 in 9 Springer DOIs (`10.1007/s00415-019-09213-x`), not just ORCIDs and ISBN-13: all three are exempted in `memory.NOT_CARDS`; a fact dropped as "looks like a secret" is silent apart from a gap.
51. ruff 0.17 flags a `list[str] = []` tool default (B006) even though pyproject selects only "I": keep `= []` with `# noqa: B006` (never mutate it); ADK then declares a plain string array with `default: []`, while `list[str] | None = None` would put a null type in the schema.
52. Firecrawl PDF `metadata.numPages` is the pages parsed (capped by `parsers[].maxPages`); the true count is `totalPages` (may be missing): tool 7 reports `totalPages` first.
53. CourtListener storage PDFs (`storage.courtlistener.com/<filepath_local>`) need no token and are not API calls: send the token only to the API (`SEARCH`) URLs; they have their own rate limit (`courtlistener_storage`).
54. Docket filings: query `type=rd`, `q=docket_id:<n> AND is_available:true`; unfiltered result pages are mostly non-public filings.
55. pypdf given a path reads the whole file into memory: pass an open file instead (court files have no size cap).
56. rapidocr 3.x needs onnxruntime installed separately; rapidocr_onnxruntime 1.x will not install on Python 3.13+.
57. ruff ASYNC230 flags opening a file in async code: do file work in `asyncio.to_thread`.
58. `Ctx.keep_file` skips the `API_HOSTS` rule on purpose (court files come from CourtListener): never use it for page fetches (gotcha 40).
59. A tool reply over `RESULT_MAX` is dropped whole by the wrapper: replies with file or case lists must be capped or cut (`court_records` uses `fit`).
60. `store.new_id` ids (prefix + 16 hex) can hold 12+ digits in a row and failed the card check (~2% each, so ~1 in 30 court files with two ids in its facts was dropped): `tools.secret` blanks `tools.OWN_IDS` first, only ids with an a-f letter (an all-digit one may hide a card number).
