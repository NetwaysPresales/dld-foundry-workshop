from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterable


def load_dataset(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _normalize(value: str) -> str:
    value = value.replace("\u2019", "'").replace("\u2018", "'").replace("**", "")
    return re.sub(r"\s+", " ", value.casefold()).strip()


def deterministic_score(case: dict[str, Any], answer: str) -> dict[str, Any]:
    normalized = _normalize(answer)
    facts = case.get("expected_facts", [])
    forbidden = case.get("forbidden_claims", [])
    sources = case.get("required_sources", [])
    fact_hits = [fact for fact in facts if _normalize(fact) in normalized]
    forbidden_hits = [claim for claim in forbidden if _normalize(claim) in normalized]
    source_hits = [
        source for source in sources if _normalize(source).replace("_", " ") in normalized
    ]
    return {
        "id": case["id"],
        "fact_recall": len(fact_hits) / len(facts) if facts else 1.0,
        "citation_recall": len(source_hits) / len(sources) if sources else 1.0,
        "forbidden_claims": forbidden_hits,
        "safe": not forbidden_hits,
        "passed": len(fact_hits) == len(facts) and not forbidden_hits,
    }


def run_cloud_evaluation(
    settings: Any,
    credential: Any,
    *,
    agent_name: str,
    cases: list[dict[str, Any]],
    judge_model: str = "gpt-5.4-mini",
    poll_seconds: int = 10,
    timeout_seconds: int = 1200,
) -> dict[str, Any]:
    """Run a Foundry cloud evaluation: the service queries the agent, then scores each answer."""
    import time

    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import TestingCriterionAzureAIEvaluator

    client = AIProjectClient(settings.project_endpoint, credential).get_openai_client()

    def criterion(name: str, evaluator: str, *, judge: bool) -> Any:
        return TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name=name,
            evaluator_name=evaluator,
            initialization_parameters={"deployment_name": judge_model} if judge else None,
            data_mapping={"query": "{{item.query}}", "response": "{{sample.output_text}}"},
        )

    evaluation = client.evals.create(
        name=f"{agent_name}-workshop-evaluation",
        data_source_config={
            "type": "custom",
            "item_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "include_sample_schema": True,
        },
        testing_criteria=[
            criterion("Relevance", "builtin.relevance", judge=True),
            criterion("Coherence", "builtin.coherence", judge=True),
            criterion("Violence", "builtin.violence", judge=False),
        ],
    )
    run = client.evals.runs.create(
        eval_id=evaluation.id,
        name=f"{agent_name}-run",
        data_source={
            "type": "azure_ai_target_completions",
            "source": {
                "type": "file_content",
                "content": [{"item": {"query": case["query"]}} for case in cases],
            },
            "input_messages": {
                "type": "template",
                "template": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": {"type": "input_text", "text": "{{item.query}}"},
                    }
                ],
            },
            "target": {"type": "azure_ai_agent", "name": agent_name},
        },
    )
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        run = client.evals.runs.retrieve(run_id=run.id, eval_id=evaluation.id)
        if run.status in ("completed", "failed", "canceled"):
            break
        time.sleep(poll_seconds)
    summary = run.model_dump() if hasattr(run, "model_dump") else dict(run)
    return {
        "status": summary.get("status"),
        "report_url": summary.get("report_url"),
        "result_counts": summary.get("result_counts"),
        "per_testing_criteria_results": summary.get("per_testing_criteria_results"),
        "error": summary.get("error"),
    }


def summarize(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(results)
    return {
        "cases": len(rows),
        "pass_rate": sum(bool(row["passed"]) for row in rows) / len(rows) if rows else 0,
        "mean_fact_recall": sum(row["fact_recall"] for row in rows) / len(rows) if rows else 0,
        "mean_citation_recall": sum(row["citation_recall"] for row in rows) / len(rows) if rows else 0,
        "safety_failures": [row["id"] for row in rows if not row["safe"]],
    }
