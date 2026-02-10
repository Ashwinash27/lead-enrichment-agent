import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")

from agent.orchestrator import enrich_lead
from agent.schemas import EnrichRequest


async def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "Saarth Shah"
    company = sys.argv[2] if len(sys.argv) > 2 else "Sixtyfour"

    print(f"{'=' * 60}")
    print(f"Lead Research Agent — Test Run")
    print(f"Name:    {name}")
    print(f"Company: {company}")
    print(f"{'=' * 60}\n")

    request = EnrichRequest(name=name, company=company)
    response = await enrich_lead(request)

    print(json.dumps(response.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
