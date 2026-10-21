from __future__ import annotations

from time import perf_counter
from typing import Any

from azure.ai.projects import AIProjectClient

from .environment import WorkshopSettings


def compare_models(
    settings: WorkshopSettings,
    credential: Any,
    prompts: list[dict[str, str]],
    deployments: tuple[str, ...] = ("gpt-5.4-mini", "model-router"),
) -> list[dict[str, Any]]:
    client = AIProjectClient(settings.project_endpoint, credential).get_openai_client()
    rows = []
    for item in prompts:
        for deployment in deployments:
            started = perf_counter()
            response = client.responses.create(
                model=deployment,
                input=item["prompt"],
                max_output_tokens=900,
                reasoning={"effort": item.get("reasoning_effort", "medium")},
            )
            usage = response.usage.model_dump() if response.usage else {}
            rows.append(
                {
                    "prompt_id": item["id"],
                    "complexity": item["complexity"],
                    "deployment": deployment,
                    "serving_model": response.model,
                    "latency_seconds": round(perf_counter() - started, 3),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "answer": response.output_text,
                }
            )
    return rows


DEFAULT_ROUTING_PROMPTS = [
    {
        "id": "simple-classify",
        "complexity": "simple",
        "reasoning_effort": "low",
        "prompt": "In one word, is this email a rent enquiry, a complaint, or spam? 'Is my landlord allowed to raise my rent by 15%?'",
    },
    {
        "id": "moderate-summary",
        "complexity": "moderate",
        "reasoning_effort": "medium",
        "prompt": (
            "Summarise this tenant email for a DLD staff member in three bullet points (facts, question, "
            "missing information): 'My landlord will raise my rent by 15% when my contract renews on 1 March. "
            "I pay AED 80,000. He told me by WhatsApp last week. Is this allowed?'"
        ),
    },
    {
        "id": "complex-reasoning",
        "complexity": "complex",
        "reasoning_effort": "high",
        "prompt": (
            "A Dubai tenant pays AED 92,000; the RERA index average for similar units is AED 120,000. The "
            "contract expires on 31 January and the landlord's written notice of a 12% increase arrived on "
            "15 November. Under Decree 43 of 2013 (caps: up to 10% below average 0%; 11-20% below 5%; 21-30% "
            "below 10%; 31-40% below 15%; over 40% below 20%) and the 90-day notice rule, work out step by step "
            "whether the increase and the notice are valid, the maximum lawful new rent, and what the tenant "
            "should do. Then list the two assumptions you relied on."
        ),
    },
]
