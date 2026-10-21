"""Anti-hallucination techniques, each small enough to demonstrate on its own.

1. Confidence: semantic reranker scores (0-4) per passage, and refusing below a threshold.
2. Groundedness detection: Azure AI Content Safety flags sentences the sources don't support.
3. Citation check: every cited document must be among the passages actually retrieved.
4. Conversation context: Foundry IQ plans searches from the whole conversation, not just the last line.
5. Strict vs. loose instructions: the same evidence with and without "answer only from the sources".
"""

from __future__ import annotations

import re
from typing import Any

import requests
from azure.ai.projects import AIProjectClient
from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
from azure.search.documents.knowledgebases.models import (
    AzureBlobKnowledgeSourceParams,
    KnowledgeBaseMessage,
    KnowledgeBaseMessageTextContent,
    KnowledgeBaseRetrievalRequest,
    KnowledgeRetrievalLowReasoningEffort,
    KnowledgeRetrievalOutputMode,
)

from .environment import WorkshopSettings
from .knowledge import CHAT_DEPLOYMENT, KNOWLEDGE_BASE, KNOWLEDGE_SOURCE, classic_retrieve


STRICT = (
    "Answer ONLY from the passages provided. Cite the source file name after every claim, "
    "like (01_rent_increase_rules.pdf). If the passages don't contain the answer, "
    "say exactly: 'The rental documents don't cover this.' Never use outside knowledge."
)
# A realistic badly-written prompt: it pressures the model to always be specific.
LOOSE = (
    "You are a confident customer-service expert. Customers hate vague answers: always give a "
    "specific amount and a specific time. Never say you don't know."
)
CITATION = re.compile(r"\b(\d{2}_[a-z0-9_]+)(?:\.pdf)?\b", re.IGNORECASE)
CITATION_TAG = re.compile(r"\s*\((?:[^()]*\d{2}_[a-z0-9_]+(?:\.pdf)?[^()]*)\)", re.IGNORECASE)


def _model(settings: WorkshopSettings, credential: Any, instructions: str, prompt: str) -> str:
    client = AIProjectClient(settings.project_endpoint, credential).get_openai_client()
    response = client.responses.create(
        model=CHAT_DEPLOYMENT, instructions=instructions, input=prompt, max_output_tokens=900
    )
    return response.output_text or ""


def _passages_prompt(question: str, passages: list[dict[str, Any]]) -> str:
    context = "\n\n".join(f"SOURCE: {p['source_file']}\n{p['content']}" for p in passages)
    return f"QUESTION:\n{question}\n\nPASSAGES:\n{context}"


# 1 ── Confidence ────────────────────────────────────────────────────────────────

def scored_passages(settings: WorkshopSettings, credential: Any, question: str, top: int = 5) -> list[dict[str, Any]]:
    """Retrieve passages with the semantic reranker's relevance score (0 = unrelated, 4 = highly relevant)."""
    rows = []
    for item in classic_retrieve(settings, credential, question, top=top):
        rows.append({
            "source_file": item["source_file"],
            "score": round(item.get("@search.reranker_score") or 0.0, 2),
            "content": item["content"],
            "preview": item["content"][:110].replace("\n", " ") + "…",
        })
    return rows


def confidence_label(score: float) -> str:
    return "High" if score >= 2.5 else "Medium" if score >= 1.8 else "Low"


def answer_with_confidence(
    settings: WorkshopSettings,
    credential: Any,
    question: str,
    threshold: float = 1.8,
) -> dict[str, Any]:
    """Answer only from passages above the threshold; refuse without calling the model if none qualify."""
    passages = scored_passages(settings, credential, question)
    kept = [p for p in passages if p["score"] >= threshold]
    best = max((p["score"] for p in passages), default=0.0)
    if not kept:
        answer = (f"I don't have enough evidence to answer this. The best matching passage scored "
                  f"{best} out of 4, below the {threshold} threshold, so I didn't ask the model to guess.")
    else:
        answer = _model(settings, credential, STRICT, _passages_prompt(question, kept))
    return {
        "question": question,
        "best_score": best,
        "confidence": confidence_label(best),
        "passages_used": len(kept),
        "answered": bool(kept),
        "answer": answer,
        "passages": passages,
    }


# 2 ── Groundedness detection ────────────────────────────────────────────────────

def detect_groundedness(
    settings: WorkshopSettings,
    credential: Any,
    *,
    question: str,
    answer: str,
    sources: list[str],
) -> dict[str, Any]:
    """Ask Azure AI Content Safety which parts of the answer the sources don't support."""
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    response = requests.post(
        f"https://{settings.foundry_account_name}.cognitiveservices.azure.com"
        "/contentsafety/text:detectGroundedness?api-version=2024-09-15-preview",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "domain": "Generic",
            "task": "QnA",
            "qna": {"query": question},
            # Citation tags aren't claims; the citation check verifies them separately.
            "text": CITATION_TAG.sub("", answer),
            "groundingSources": sources,
            "reasoning": False,
        },
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()
    return {
        "ungrounded": result.get("ungroundedDetected", False),
        "ungrounded_percentage": round(100 * result.get("ungroundedPercentage", 0), 1),
        "ungrounded_sentences": [d.get("text", "").strip() for d in result.get("ungroundedDetails", [])],
    }


# 3 ── Citation check ────────────────────────────────────────────────────────────

def verify_citations(answer: str, passages: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the documents the answer cites with the documents actually retrieved."""
    retrieved = {p["source_file"].lower().removesuffix(".pdf") for p in passages}
    cited = {match.lower() for match in CITATION.findall(answer)}
    return {
        "cited": sorted(cited),
        "verified": sorted(cited & retrieved),
        "not_in_evidence": sorted(cited - retrieved),
        "ok": bool(cited) and not (cited - retrieved),
    }


# 4 ── Conversation context ──────────────────────────────────────────────────────

def foundry_iq_turn(
    settings: WorkshopSettings,
    credential: Any,
    conversation: list[tuple[str, str]],
    *,
    reranker_threshold: float | None = None,
) -> dict[str, Any]:
    """Run Foundry IQ on a whole conversation [(role, text), ...] and return answer, searches and references."""
    client = KnowledgeBaseRetrievalClient(settings.search_endpoint, credential, knowledge_base_name=KNOWLEDGE_BASE)
    response = client.retrieve(
        KnowledgeBaseRetrievalRequest(
            messages=[
                KnowledgeBaseMessage(role=role, content=[KnowledgeBaseMessageTextContent(text=text)])
                for role, text in conversation
            ],
            include_activity=True,
            output_mode=KnowledgeRetrievalOutputMode.ANSWER_SYNTHESIS,
            retrieval_reasoning_effort=KnowledgeRetrievalLowReasoningEffort(),
            knowledge_source_params=[
                AzureBlobKnowledgeSourceParams(
                    knowledge_source_name=KNOWLEDGE_SOURCE,
                    include_references=True,
                    reranker_threshold=reranker_threshold,
                )
            ],
        )
    ).as_dict()
    answer = "".join(
        part.get("text", "") for message in response.get("response") or [] for part in message.get("content", [])
    )
    searches = [
        step.get("azureBlobArguments", {}).get("search") or step.get("searchIndexArguments", {}).get("search")
        for step in response.get("activity") or []
        if step.get("type") not in ("modelQueryPlanning", "modelAnswerSynthesis", "agenticReasoning")
    ]
    references = [
        {
            "ref": ref.get("id"),
            "document": (ref.get("blobUrl") or "").rsplit("/", 1)[-1] or ref.get("docKey"),
            "score": round(ref.get("rerankerScore") or 0.0, 2),
        }
        for ref in response.get("references") or []
    ]
    return {"answer": answer, "searches": [s for s in searches if s], "references": references}


# 5 ── Strict vs. loose instructions ─────────────────────────────────────────────

def strict_vs_loose(settings: WorkshopSettings, credential: Any, question: str) -> dict[str, Any]:
    """Same retrieved passages, two instruction styles."""
    passages = scored_passages(settings, credential, question)
    prompt = _passages_prompt(question, passages)
    return {
        "loose": _model(settings, credential, LOOSE, prompt),
        "strict": _model(settings, credential, STRICT, prompt),
        "sources": [p["content"] for p in passages],
    }
