# Resume Bullet Points

- Built an AI-powered lead enrichment agent that researches 7 public data sources concurrently (GitHub, Google Search, Google News, HN/Reddit, headless browser, SMTP, Hunter.io) and returns a structured profile with talking points in under 45 seconds, achieving 100% accuracy across 10 ground truth eval cases with 0 crashes

- Designed a LangGraph StateGraph pipeline with fan-out/fan-in parallelism across 4 execution phases, reducing end-to-end latency to ~39s per enrichment at ~$0.03/request by limiting the architecture to exactly 2 LLM calls (planner + extractor) with all tool execution running concurrently between them

- Implemented a 4-layer email waterfall (GitHub public email → regex scan → SMTP RCPT TO verification → Hunter.io API) that exhausts 3 free discovery methods before consuming paid API credits, with domain extraction from browser results and per-layer confidence scoring

- Added semantic caching with Qdrant vector search and OpenAI embeddings that delivers 21x faster responses (~2s vs ~39s) on repeat or near-duplicate lookups, with cosine similarity thresholding to handle name/company variations

- Engineered resilience across all external API calls with exponential backoff retry (1s/2s/4s + jitter, 5xx/timeout only), structured LLM output retry on truncation detection, and Pydantic field validators that coerce schema drift from smaller models — achieving 10/10 eval pass rate where the baseline scored 88%
