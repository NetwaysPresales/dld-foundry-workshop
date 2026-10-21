from __future__ import annotations

from typing import Any

import requests

from .environment import WorkshopSettings


def prompt_shields(
    settings: WorkshopSettings,
    credential: Any,
    *,
    user_prompt: str,
    documents: list[str] | None = None,
) -> dict[str, Any]:
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    endpoint = (
        f"https://{settings.foundry_account_name}.cognitiveservices.azure.com"
        "/contentsafety/text:shieldPrompt?api-version=2024-09-01"
    )
    response = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"userPrompt": user_prompt, "documents": documents or []},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def enforce_prompt_shields(result: dict[str, Any]) -> None:
    user_attack = result.get("userPromptAnalysis", {}).get("attackDetected", False)
    document_attack = any(
        item.get("attackDetected", False)
        for item in result.get("documentsAnalysis", [])
    )
    if user_attack or document_attack:
        raise PermissionError(
            "Prompt Shields detected an attack. The content was not sent to tools or agents."
        )
