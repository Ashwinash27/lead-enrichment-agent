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
    location = sys.argv[3] if len(sys.argv) > 3 else ""
    use_case = sys.argv[4] if len(sys.argv) > 4 else "sales"

    print(f"{'=' * 60}")
    print(f"Lead Research Agent — Test Run")
    print(f"Name:     {name}")
    print(f"Company:  {company}")
    if location:
        print(f"Location: {location}")
    print(f"Use Case: {use_case}")
    print(f"{'=' * 60}\n")

    request = EnrichRequest(name=name, company=company, location=location, use_case=use_case)
    response = await enrich_lead(request)

    print(json.dumps(response.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
