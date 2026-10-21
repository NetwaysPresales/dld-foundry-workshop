"""Event-driven mode: the Logic App that reacts to new "DLD rent" emails."""

from __future__ import annotations

from typing import Any

import requests

from .environment import WorkshopSettings


def _arm(credential: Any, path: str, api_version: str) -> dict[str, Any]:
    token = credential.get_token("https://management.azure.com/.default").token
    response = requests.get(
        f"https://management.azure.com{path}?api-version={api_version}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _group(settings: WorkshopSettings) -> str:
    return f"/subscriptions/{settings.subscription_id}/resourceGroups/{settings.resource_group}"


def outlook_connection_status(settings: WorkshopSettings, credential: Any) -> str:
    """'Connected' once the attendee has authorised the Outlook connection in the portal."""
    connection = _arm(
        credential,
        f"{_group(settings)}/providers/Microsoft.Web/connections/{settings.outlook_connection_name}",
        "2016-06-01",
    )
    statuses = connection.get("properties", {}).get("statuses") or [{}]
    return statuses[0].get("status", "Unknown")


def logic_app_runs(settings: WorkshopSettings, credential: Any, top: int = 5) -> list[dict[str, Any]]:
    """Most recent Logic App runs with their status and the actions they reached."""
    base = f"{_group(settings)}/providers/Microsoft.Logic/workflows/{settings.logic_app_name}"
    runs = _arm(credential, f"{base}/runs", "2019-05-01").get("value", [])[:top]
    rows = []
    for run in runs:
        actions = _arm(credential, f"{base}/runs/{run['name']}/actions", "2019-05-01").get("value", [])
        done = [a["name"] for a in sorted(actions, key=lambda a: a["properties"].get("startTime") or "")
                if a["properties"].get("status") == "Succeeded"]
        waiting = [a["name"] for a in actions if a["properties"].get("status") in ("Running", "Waiting")]
        rows.append({
            "started": run["properties"].get("startTime", "")[:19].replace("T", " "),
            "status": run["properties"].get("status"),
            "steps completed": " → ".join(done) or "-",
            "waiting on": ", ".join(waiting) or "-",
        })
    return rows


def portal_links(settings: WorkshopSettings) -> dict[str, str]:
    base = f"https://portal.azure.com/#@{settings.tenant_id}/resource{_group(settings)}/providers"
    return {
        "Authorise Outlook": f"{base}/Microsoft.Web/connections/{settings.outlook_connection_name}/edit",
        "Logic App runs": f"{base}/Microsoft.Logic/workflows/{settings.logic_app_name}/logicApp",
    }
