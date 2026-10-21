from __future__ import annotations

from typing import Any

from azure.ai.projects import AIProjectClient

from .environment import WorkshopSettings


def agent_identity(
    settings: WorkshopSettings,
    credential: Any,
    agent_name: str,
) -> dict[str, str]:
    """Return the agent's own Microsoft Entra identity (its Agent ID)."""
    details = AIProjectClient(settings.project_endpoint, credential).agents.get(agent_name)
    identity = details.instance_identity
    if not identity:
        raise RuntimeError(f"{agent_name} does not expose an instance identity yet.")
    return {
        "agent": agent_name,
        "principal_id": identity.principal_id,
        "client_id": identity.client_id,
        "status": str(getattr(identity.status, "value", identity.status)),
    }
