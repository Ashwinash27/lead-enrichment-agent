import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.orchestrator import enrich_lead
from agent.schemas import EnrichRequest, EnrichResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Lead Research Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/enrich", response_model=EnrichResponse)
async def enrich(request: EnrichRequest):
    return await enrich_lead(request)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
