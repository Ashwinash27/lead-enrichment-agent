# Codebase Concerns

**Analysis Date:** 2026-02-23

---

## Tech Debt

**Dead code — legacy orchestrator kept alongside new graph-based one:**
- Issue: `agent/orchestrator_legacy.py` (211 lines) implements the full pipeline identically to `agent/graph.py` + `agent/orchestrator.py`. Both files are maintained, creating a divergence risk. The live path is `agent/orchestrator.py` → `agent/graph.py`, but the legacy file is not clearly marked as obsolete and could be accidentally imported.
- Files: `agent/orchestrator_legacy.py`, `agent/orchestrator.py`, `agent/graph.py`
- Impact: Anyone adding a feature to one file must remember to update (or consciously skip) the other. Dead code inflates codebase size by ~250 lines.
- Fix approach: Delete `agent/orchestrator_legacy.py` and update any references.

**`hunter_tool.py` is unreachable dead code:**
- Issue: `tools/hunter_tool.py` (128 lines) is a standalone `HunterIoTool` class that is NOT registered in `tools/__init__.py` and NOT called anywhere. The actual Hunter calls happen inside `tools/email_pipeline.py` via `_layer_hunter()`. The standalone tool is therefore completely unused and provides a confusing duplicate of the same logic with different parameter handling.
- Files: `tools/hunter_tool.py`, `tools/email_pipeline.py`
- Impact: Misleads future developers who may register it again or expect it to function.
- Fix approach: Delete `tools/hunter_tool.py`. All Hunter.io logic is owned by `email_pipeline.py`.

**Semantic cache ignores `use_case` in lookup queries:**
- Issue: `agent/semantic_cache.py`'s `lookup()` function embeds only `name` and `company` in the embedding query text (via `_normalize()`). A cached `sales` response may be returned for a `recruiting` request with identical name/company, since `use_case` is only stored in payload, not used in similarity search.
- Files: `agent/semantic_cache.py` lines 68–69, 86–101
- Impact: Wrong talking points style returned for cached hits. Stores `use_case` in payload (line 155) but never reads it during lookup to filter.
- Fix approach: Include `use_case` in the normalized embedding text: `f"{name.lower()} {company.lower()} {use_case.lower()}"`, or add a post-lookup payload filter on `use_case`.

**In-memory cache has no size cap or proactive eviction:**
- Issue: `agent/cache.py` `InMemoryCache._store` grows unbounded. Entries are evicted lazily only on `get()` for that specific key. Under sustained load, every unique query string adds an entry that stays until re-fetched or process restart.
- Files: `agent/cache.py`
- Impact: Memory leak in long-running server processes. A 30-day running server enriching thousands of leads accumulates all search queries.
- Fix approach: Add a max-size LRU cap (e.g., `maxsize=1000`) or use `functools.lru_cache` / `cachetools.TTLCache`.

**`MAX_CONTEXT` in extractor does not match CLAUDE.md documented limit:**
- Issue: `CLAUDE.md` states "Truncate LLM context to 30k chars max" but `agent/extractor.py` line 20 sets `MAX_CONTEXT = 20000`. The talking points builder uses `15000`. These are safe underestimates but the documentation mismatch may confuse future maintainers who apply the 30k limit elsewhere.
- Files: `agent/extractor.py` lines 20–22, `CLAUDE.md`
- Impact: Minor — no runtime harm, but documentation drift.
- Fix approach: Update `CLAUDE.md` to reflect actual limits or raise constants to match.

---

## Known Bugs

**SMTP verification fires port-25 connections even in cloud environments where outbound port 25 is blocked:**
- Symptoms: Layer 3 always times out (8 s) for every request running in cloud environments (AWS, GCP, Azure, Heroku all block outbound port 25 by default), wasting 8 seconds per enrichment unnecessarily.
- Files: `tools/email_pipeline.py` lines 172–220, `_layer_smtp`, `_smtp_verify`
- Trigger: Any enrichment where GitHub/regex layers don't find an email.
- Workaround: Set `SMTP_LAYER_TIMEOUT = 0` or comment out the SMTP layer call in `run()` when running in a cloud environment.

**Talking points and extraction run on independent context slices with different truncation limits:**
- Symptoms: Extractor uses `PER_TOOL_MAX=3000` per tool + `MAX_CONTEXT=20000` total; talking points builder uses `3000` per tool + `15000` total (lines 408, 411). The same tool result may be seen differently by each LLM call, causing inconsistency between `profile` and `talking_points`.
- Files: `agent/extractor.py` lines 405–412, `agent/extractor.py` lines 180–185
- Trigger: Any request with rich tool results (total > 15k chars), which is common.
- Workaround: None. By design they share the same raw data but it gets truncated independently.

**`_repair_truncated_json_object` has O(n²) worst-case performance:**
- Symptoms: When the extractor hits `max_tokens` on a large response, the repair function runs a double loop scanning up to 2000 characters backwards with rfind for each of 3 trim characters. On malformed JSON, this can iterate tens of thousands of times.
- Files: `agent/extractor.py` lines 136–172
- Trigger: Extractor hits `INITIAL_MAX_TOKENS=4000` token limit with a poorly-structured JSON output.
- Workaround: The 2000-char scan window limits the blast radius, but the loop is still quadratic within that window.

**GitHub `_search_user` returns first result regardless of company match:**
- Symptoms: For common names (e.g., "David Smith"), the GitHub tool returns the first result from the search API, which may not be the correct person. There is no post-search validation that the found user's company field matches the requested company.
- Files: `tools/github_tool.py` lines 97–108, `_search_user`, `_try_search`
- Trigger: Common names where the correct person is not the top GitHub search result.
- Workaround: None. Fallback searches (line 101–102) only broaden the query, not narrow it.

---

## Security Considerations

**API has no authentication or authorization:**
- Risk: The `/enrich` endpoint is completely open. Any caller can trigger enrichment pipelines that consume paid API credits (Serper, Hunter.io, Anthropic) without restriction.
- Files: `main.py` lines 30–32
- Current mitigation: None.
- Recommendations: Add API key header validation middleware or OAuth2, even if just a static shared secret for single-tenant use.

**CORS allows all origins with credentials:**
- Risk: `allow_origins=["*"]` combined with `allow_credentials=True` is a misconfiguration. Browsers reject `allow_credentials=True` with wildcard origins per the CORS spec, but the intent to allow credentials from any origin is a security risk if the server is deployed publicly.
- Files: `main.py` lines 16–22
- Current mitigation: The combination is rejected by browsers, so it doesn't actively leak credentials, but it signals missing CORS hardening.
- Recommendations: Set `allow_origins` to an explicit list of trusted origins. Remove `allow_credentials=True` unless session cookies are intentionally used.

**SSL certificate verification is disabled for ALL browser scraping (not just proxy mode):**
- Risk: `ignore_https_errors=True` is set in `browser.new_context()` unconditionally (line 115), even when not using a proxy. This means the browser will silently accept MITM'd or self-signed certificates for any site being scraped, and malicious content served over a fake HTTPS connection would be trusted.
- Files: `tools/playwright_tool.py` lines 113–116
- Current mitigation: None. The flag was likely added to support proxy certificate injection but was not scoped to proxy-only paths.
- Recommendations: Move `ignore_https_errors=True` inside the `if proxy_cfg:` branch only.

**No rate limiting per caller, enabling runaway API cost attacks:**
- Risk: A single unauthenticated caller can submit hundreds of concurrent `/enrich` requests, each triggering multiple paid API calls (Anthropic, Serper, Hunter.io). There is no request queue, concurrency cap, or per-IP throttling.
- Files: `main.py`, `agent/orchestrator.py`
- Current mitigation: None.
- Recommendations: Add a semaphore in `orchestrator.py` to cap concurrent enrichments, or use a FastAPI rate limiter middleware (e.g., `slowapi`).

**Proxy credentials embedded in URLs in plain-text proxy strings:**
- Risk: `PROXY_LIST` env var stores proxy URLs with embedded credentials in the format `http://user:password@host:port`. These are logged directly when a proxy is selected: `logger.info(f"Using proxy: {proxy_cfg['server']}")`. The server portion strips credentials, but the raw list URL is visible in process environment.
- Files: `tools/proxy.py`, `tools/playwright_tool.py` line 108
- Current mitigation: Log statement uses the parsed server URL (not the full URL), but credentials still live in environment as plaintext.
- Recommendations: Store proxy credentials as separate env vars, not embedded in URL strings.

---

## Performance Bottlenecks

**SMTP layer adds mandatory 8s timeout per enrichment when it fails (the common case):**
- Problem: The SMTP layer always runs and always waits up to `SMTP_LAYER_TIMEOUT=8` seconds before giving up, even in environments where port 25 is unreachable (most cloud VMs). This adds 8 seconds to the critical path for every enrichment where GitHub and regex layers fail, which is the majority of cases.
- Files: `tools/email_pipeline.py` lines 174–190
- Cause: No environment detection; SMTP verification fires unconditionally.
- Improvement path: Add a startup check that tests port 25 connectivity and sets a flag to skip SMTP if the port is blocked. Alternatively, make SMTP opt-in via an env var `SMTP_EMAIL_VERIFY=true`.

**New Playwright instance is spawned per URL scrape (no browser reuse):**
- Problem: `PlaywrightTool._scrape_url()` calls `async_playwright().start()` and `pw.chromium.launch()` on every invocation. Browser startup is expensive (typically 500–2000ms). With up to 3 URLs scraped per request (graph.py line 22), this means up to 3 full browser launches per enrichment.
- Files: `tools/playwright_tool.py` lines 99–145
- Cause: No persistent browser context or connection pool.
- Improvement path: Use a module-level persistent browser instance with a connection pool, or use `browser.new_context()` for isolation instead of relaunching.

**GitHub user search has no short-circuit for clearly non-technical people:**
- Problem: The planner always runs GitHub search for non-technical leads (e.g., enterprise sales targets), consuming 4 concurrent API calls (profile, repos, starred, events) for a lookup that will yield no meaningful data.
- Files: `agent/graph.py` lines 73–118, `tools/github_tool.py` lines 60–65
- Cause: `deterministic_tools_node` always runs `["github", "news", "community"]` regardless of planner decision. The planner's GitHub recommendation is ignored for Phase A.
- Improvement path: Pass the planner decision to `deterministic_tools_node` (requires graph re-wiring), or move GitHub to Phase B (planner-dependent). Currently, Phase A always runs GitHub whether the planner requested it or not.

**Talking points generation is a full LLM call even when no tool data exists:**
- Problem: `generate_talking_points()` is called concurrently with `extract()` in Phase C regardless of how much data was collected. When most tools fail, it calls `claude-sonnet-4` with near-empty context and returns generic results.
- Files: `agent/graph.py` lines 222–234, `agent/extractor.py` lines 396–450
- Cause: No guard on minimum data quality before firing the LLM call.
- Improvement path: Skip talking points generation if fewer than 2 tool results succeeded, returning an empty list directly.

---

## Fragile Areas

**`agent/observe.py` `traced_node` wrapper silently converts all state values to strings for sizing:**
- Files: `agent/observe.py` lines 123–129
- Why fragile: `str(state.get(k, ""))` on complex objects like `list[ToolResult]` is not just slow—it serializes the entire Pydantic model list to string for a character count. On a large result set (e.g., 10 tool results with 3000 chars each), this costs 30k+ char serialization just for logging metadata. Worse, it iterates all state keys including `tool_results` which can be large.
- Safe modification: Skip `tool_results` and `profile` from the input_size calculation, or use `len(tool_results)` (count) rather than `len(str(tool_results))` (serialized size).
- Test coverage: None.

**GitHub tool mutates instance state on auth failure (`self._use_auth = False`):**
- Files: `tools/github_tool.py` lines 91–94
- Why fragile: `GitHubTool._request()` sets `self._use_auth = False` when a 401 is received. Since the tool instance is shared (registered in a singleton registry), this permanent mutation means all future requests in the same process will use unauthenticated GitHub API after any single auth failure. If the token is temporarily invalid (e.g., rate limit edge case), the fallback is permanent for the process lifetime.
- Safe modification: Do not mutate `self._use_auth`. Instead track failure in a local variable or raise so the caller decides.
- Test coverage: None.

**Deferred `import asyncio` and `import dns.resolver` inside hot-path async methods:**
- Files: `tools/email_pipeline.py` lines 183, 195, 225–226, 242
- Why fragile: `asyncio` and `dns.resolver` are imported inside the body of async methods called on every enrichment. Python caches module imports, so this is a minor runtime cost (dict lookup), but it obscures the dependency graph and makes the code harder to analyze. `dns.resolver` not being in the top-level imports means a missing `dnspython` package would only surface at runtime during the email pipeline.
- Safe modification: Move all imports to the top of `email_pipeline.py`.
- Test coverage: None.

**`_build_system_prompt()` is called per extraction attempt, rebuilding a multi-KB string each time:**
- Files: `agent/extractor.py` lines 26–133, called at lines 230
- Why fragile: The system prompt is 3600+ characters and is rebuilt on every call to `_extract_once()`. On retry paths (when `_needs_retry()` returns True), this function is called twice per enrichment. The prompt is static except for `date.today()`, so rebuilding it from scratch with string interpolation is wasteful.
- Safe modification: Cache the prompt per-day using `functools.lru_cache` with a date-keyed wrapper.
- Test coverage: None.

**`asyncio.get_event_loop()` usage is deprecated in Python 3.10+:**
- Files: `tools/email_pipeline.py` line 228, `tools/playwright_tool.py` line 92
- Why fragile: `asyncio.get_event_loop()` emits a `DeprecationWarning` in Python 3.10+ when called without a running event loop. In these files it is called from within async methods (so a loop exists), but the recommended replacement is `asyncio.get_running_loop()`.
- Safe modification: Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()` in both locations.
- Test coverage: None.

---

## Scaling Limits

**In-memory cache is not shared across FastAPI worker processes:**
- Current capacity: Single process only. `agent/cache.py` uses a module-level `InMemoryCache` singleton.
- Limit: When deployed with multiple workers (e.g., `uvicorn --workers 4`), each process has its own isolated cache. Cached tool results are not shared, so the same GitHub search is executed 4x independently.
- Scaling path: Replace `InMemoryCache` with a Redis-backed cache (e.g., `aioredis`). The `Cache` Protocol in `agent/cache.py` makes this a drop-in swap.

**Semantic cache lookup is not deduplicated for concurrent identical requests:**
- Current capacity: Works correctly for sequential requests.
- Limit: If two concurrent requests for the same person arrive simultaneously, both will miss the semantic cache (since the first hasn't stored yet) and both will run the full pipeline. This doubles API costs for burst traffic on the same lead.
- Scaling path: Add a request-level in-flight deduplication map (e.g., `asyncio.Lock` keyed by normalized name+company+use_case).

**Hunter.io is hard-limited to 25 lookups/month on the free tier:**
- Current capacity: 25 email lookups/month.
- Limit: Exceeded on any meaningful production load. No circuit breaker exists — it will silently return "no email found" after the quota is exhausted.
- Scaling path: Upgrade Hunter.io plan, or track usage in a persistent counter and disable Layer 4 when quota is near exhaustion.

---

## Dependencies at Risk

**`dnspython` is a required but pinned-version-less dependency used for SMTP MX lookup:**
- Risk: `requirements.txt` specifies `dnspython` without a version pin. The `dns.resolver` API has changed between major versions. If a future `pip install` pulls a breaking version, the SMTP layer will crash at runtime (not import time, due to deferred imports).
- Files: `requirements.txt` line 10, `tools/email_pipeline.py` line 226
- Impact: SMTP email layer silently returns `("", 0.0, "")` on any exception, so breakage is masked in logs as a timeout rather than an import error.
- Migration plan: Pin `dnspython>=2.0,<3.0` in `requirements.txt`.

**All requirements are unpinned — no lockfile:**
- Risk: `requirements.txt` has no version pins for any dependency. `fastapi`, `anthropic`, `langgraph`, `qdrant-client`, `openai`, `langfuse` all have breaking changes across minor versions.
- Files: `requirements.txt`
- Impact: A fresh `pip install` on a new machine or CI environment may pull incompatible versions.
- Migration plan: Generate `requirements-lock.txt` via `pip freeze`, or adopt `uv` / `poetry` for dependency management with a lockfile.

**`langfuse` SDK version has no pin and its API has changed significantly across versions:**
- Risk: Langfuse v2/v3 has different client initialization and `trace.generation()` call signatures. An upgrade could silently break observability logging.
- Files: `agent/observe.py`, `requirements.txt`
- Impact: Langfuse failures are caught and swallowed (`except Exception: pass`), so breakage would cause silent observability loss rather than a crash.
- Migration plan: Pin `langfuse>=2.0,<3.0` or test against the specific version in CI.

---

## Missing Critical Features

**No request authentication or API key validation:**
- Problem: The `/enrich` endpoint has zero access control.
- Blocks: Cannot safely expose to the internet. Any public deployment immediately exposes the service to abuse and API credit exhaustion.

**No retry/deduplication on concurrent identical enrichment requests:**
- Problem: No in-flight request coalescing. Concurrent requests for the same person trigger parallel full pipelines.
- Blocks: Cost efficiency at scale; the same Serper/Anthropic credits are spent multiple times per unique lead.

**No mechanism to invalidate or delete stale semantic cache entries:**
- Problem: `agent/semantic_cache.py` stores cached responses for 30 days but provides no admin endpoint or CLI tool to invalidate a specific entry. If a cached profile is wrong (e.g., wrong GitHub match), it will be returned for 30 days with no recourse.
- Files: `agent/semantic_cache.py`
- Blocks: Data quality corrections for cached leads.

---

## Test Coverage Gaps

**No unit tests exist for any module:**
- What's not tested: Every module — tools, orchestrator, extractor, planner, schemas, cache, email pipeline, semantic cache.
- Files: All files in `agent/` and `tools/`. No `tests/` directory exists.
- Risk: Breaking changes to extraction logic, schema validation, or tool protocols surface only through the full eval suite (which requires live API calls and external network).
- Priority: High

**Eval suite only covers famous/easily-searchable public figures:**
- What's not tested: Private individuals, people at small companies, ambiguous names, non-English names, people with no GitHub/LinkedIn presence, partially-enriched results.
- Files: `evals/ground_truth.json` — 10 cases, all well-known public technical figures.
- Risk: Real-world accuracy for the actual target audience (sales prospects at mid-market companies) is unknown.
- Priority: High

**Email pipeline layers have no isolated tests:**
- What's not tested: Layer 1 (GitHub email parsing), Layer 2 (regex scan), Layer 3 (SMTP verify), Layer 4 (Hunter.io) can only be tested via full enrichment runs.
- Files: `tools/email_pipeline.py`
- Risk: A regression in `_is_person_email()` name-matching logic (e.g., short first names like "Ed" matching too broadly) would silently return wrong emails.
- Priority: High

**`_repair_truncated_json_object` and `_repair_truncated_json_array` have no tests:**
- What's not tested: JSON repair logic is complex (quadratic loop, multiple fallback strategies) and handles edge cases that are hard to reproduce.
- Files: `agent/extractor.py` lines 136–172, 372–393
- Risk: A subtle bug in repair logic causes silently empty profiles (returns `None` → falls back to `EnrichedProfile(name=name, company=company)`) that look like success but contain no data.
- Priority: Medium

**GitHub tool's wrong-person selection has no test:**
- What's not tested: The case where `_try_search()` returns the wrong GitHub user for an ambiguous name.
- Files: `tools/github_tool.py` lines 97–121
- Risk: Bad GitHub data poisons the extractor context with a different person's information, which the extractor may accept without detecting disambiguation failures.
- Priority: Medium

---

*Concerns audit: 2026-02-23*
