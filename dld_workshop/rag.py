"""Three grounding patterns that return the same shape, so they can be compared side by side."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from azure.ai.projects import AIProjectClient

from .environment import WorkshopSettings
from .knowledge import agentic_retrieve, classic_retrieve


SYSTEM = (
    "You are working with synthetic DLD workshop material. Distinguish confirmed facts "
    "from recommendations, never invent policy, and say when evidence is unavailable."
)


def _call_model(settings: WorkshopSettings, credential: Any, prompt: str) -> tuple[str, int]:
    client = AIProjectClient(settings.project_endpoint, credential).get_openai_client()
    response = client.responses.create(
        model="gpt-5.4-mini",
        instructions=SYSTEM,
        input=prompt,
        max_output_tokens=1200,
    )
    tokens = response.usage.total_tokens if response.usage else 0
    return response.output_text or "", tokens


def no_rag(settings: WorkshopSettings, credential: Any, query: str) -> dict[str, Any]:
    """The model answers from its own training data only."""
    started = perf_counter()
    answer, tokens = _call_model(settings, credential, query)
    return {
        "pattern": "No RAG",
        "answer": answer,
        "sources": 0,
        "searches": 0,
        "tokens": tokens,
        "seconds": round(perf_counter() - started, 1),
    }


def classic_rag(settings: WorkshopSettings, credential: Any, query: str) -> dict[str, Any]:
    """One hybrid search over the index we built ourselves, then one model call."""
    started = perf_counter()
    evidence = classic_retrieve(settings, credential, query)
    context = "\n\n".join(f"SOURCE: {item['source_file']}\n{item['content']}" for item in evidence)
    answer, tokens = _call_model(
        settings,
        credential,
        "Answer only from the supplied passages and cite source filenames.\n\n"
        f"QUESTION:\n{query}\n\nPASSAGES:\n{context}",
    )
    return {
        "pattern": "Classic RAG",
        "answer": answer,
        "sources": len({item["source_file"] for item in evidence}),
        "searches": 1,
        "tokens": tokens,
        "seconds": round(perf_counter() - started, 1),
    }


def foundry_iq(settings: WorkshopSettings, credential: Any, query: str) -> dict[str, Any]:
    """Foundry IQ plans several searches, reranks, and returns a cited answer."""
    started = perf_counter()
    retrieval = agentic_retrieve(settings, credential, query)
    messages = retrieval.get("response") or []
    answer = "".join(
        part.get("text", "")
        for message in messages
        for part in message.get("content", [])
    )
    activity = retrieval.get("activity") or []
    searches = [step for step in activity if step.get("type") not in (
        "modelQueryPlanning", "modelAnswerSynthesis", "agenticReasoning")]
    tokens = sum(step.get("inputTokens", 0) + step.get("outputTokens", 0) for step in activity)
    return {
        "pattern": "Foundry IQ",
        "answer": answer,
        "sources": len(retrieval.get("references") or []),
        "searches": len(searches),
        "tokens": tokens,
        "seconds": round(perf_counter() - started, 1),
        "search_queries": [
            step.get("searchIndexArguments", {}).get("search") or step.get("azureBlobArguments", {}).get("search")
            for step in searches
        ],
    }
