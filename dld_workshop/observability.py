"""Query workshop telemetry and cost without CLI extensions."""

from __future__ import annotations

from typing import Any

import requests

from .environment import WorkshopSettings


def _workspace_customer_id(settings: WorkshopSettings, credential: Any) -> str:
    token = credential.get_token("https://management.azure.com/.default").token
    response = requests.get(
        f"https://management.azure.com{settings.log_analytics_id}?api-version=2023-09-01",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["properties"]["customerId"]


def query_logs(settings: WorkshopSettings, credential: Any, kql: str) -> list[dict[str, Any]]:
    """Run KQL against the workspace that backs the project's Application Insights."""
    workspace = _workspace_customer_id(settings, credential)
    token = credential.get_token("https://api.loganalytics.io/.default").token
    response = requests.post(
        f"https://api.loganalytics.io/v1/workspaces/{workspace}/query",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": kql},
        timeout=120,
    )
    response.raise_for_status()
    table = response.json()["tables"][0]
    columns = [column["name"] for column in table["columns"]]
    return [dict(zip(columns, row)) for row in table["rows"]]


TRACE_SUMMARY = """
union AppRequests, AppDependencies, AppTraces
| where TimeGenerated > ago(2h)
| summarize count() by Type, AppRoleName
| order by count_ desc
"""

AGENT_SPANS = """
AppDependencies
| where TimeGenerated > ago(2h)
| where Name has_any ("invoke_agent", "execute_tool", "chat", "responses")
| project TimeGenerated, Name, DurationMs, Success, AppRoleName
| order by TimeGenerated desc
| take 25
"""


def resource_group_cost(settings: WorkshopSettings, credential: Any) -> dict[str, Any]:
    """Month-to-date actual cost by service. Cost data can lag several hours behind usage."""
    scope = f"/subscriptions/{settings.subscription_id}/resourceGroups/{settings.resource_group}"
    token = credential.get_token("https://management.azure.com/.default").token
    response = requests.post(
        f"https://management.azure.com{scope}/providers/Microsoft.CostManagement/query"
        "?api-version=2023-11-01",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "type": "ActualCost",
            "timeframe": "MonthToDate",
            "dataset": {
                "granularity": "None",
                "aggregation": {"cost": {"name": "PreTaxCost", "function": "Sum"}},
                "grouping": [{"type": "Dimension", "name": "ServiceName"}],
            },
        },
        timeout=60,
    )
    if not response.ok:
        return {"available": False, "status": response.status_code, "detail": response.text[:300]}
    result = response.json()["properties"]
    columns = [column["name"] for column in result["columns"]]
    return {"available": True, "rows": [dict(zip(columns, row)) for row in result["rows"]]}
