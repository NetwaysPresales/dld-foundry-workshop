"""Automatic prerequisite checks run before anything is deployed."""

from __future__ import annotations

from typing import Any

import requests

from .environment import WorkshopEnvironment, WorkshopSettings


# Deployment name in quota terms → capacity (thousands of tokens per minute) the template requests.
MODEL_QUOTA = {
    "OpenAI.GlobalStandard.gpt-5.4-mini": 100,
    "OpenAI.GlobalStandard.ModelRouter": 100,
    "OpenAI.Standard.text-embedding-3-large": 50,
}


def _arm_get(credential: Any, path: str) -> dict[str, Any]:
    token = credential.get_token("https://management.azure.com/.default").token
    response = requests.get(
        f"https://management.azure.com{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _result(check: str, ok: bool, detail: str, blocking: bool = True) -> dict[str, Any]:
    status = "✅ Pass" if ok else ("❌ Fix before continuing" if blocking else "⚠️ Warning")
    return {"check": check, "status": status, "detail": detail, "ok": ok or not blocking}


def run_preflight(credential: Any, settings: WorkshopSettings) -> list[dict[str, Any]]:
    results = []
    environment = WorkshopEnvironment(credential, settings)

    permissions = environment.check_permissions()
    for name, allowed in permissions.items():
        results.append(_result(
            f"Azure permission: {name}", allowed,
            "Granted" if allowed else "Ask for Owner, or Contributor + Role Based Access Control Administrator",
        ))

    usages = _arm_get(
        credential,
        f"/subscriptions/{settings.subscription_id}/providers/Microsoft.CognitiveServices"
        f"/locations/{settings.location}/usages?api-version=2024-10-01",
    ).get("value", [])
    by_name = {item["name"]["value"]: item for item in usages}
    for quota_name, needed in MODEL_QUOTA.items():
        item = by_name.get(quota_name)
        available = (item["limit"] - item["currentValue"]) if item else 0
        results.append(_result(
            f"Model quota: {quota_name.split('.', 2)[-1]} in {settings.location}",
            available >= needed,
            f"{available:.0f}K tokens/min available, {needed}K needed"
            + ("" if available >= needed else " (ignore if you are re-running your own environment)"),
            blocking=False,
        ))

    services = _arm_get(
        credential,
        f"/subscriptions/{settings.subscription_id}/providers/Microsoft.Search"
        "/searchServices?api-version=2025-05-01",
    ).get("value", [])
    own_name = f"dldsearch{settings.suffix.lower()}"
    other_free = [s["name"] for s in services if s["sku"]["name"] == "free" and s["name"] != own_name]
    free_ok = settings.search_sku != "free" or not other_free
    results.append(_result(
        "Azure AI Search tier", free_ok,
        f"Using {settings.search_sku} in {settings.search_location}"
        if free_ok else
        f"Your subscription's one Free Search slot is used by {other_free[0]}. Set SEARCH_SKU = 'basic'.",
    ))

    try:
        token = credential.get_token("https://graph.microsoft.com/.default").token
        licences = requests.get(
            "https://graph.microsoft.com/v1.0/me/licenseDetails?$select=skuPartNumber",
            headers={"Authorization": f"Bearer {token}"}, timeout=60,
        )
        skus = [item["skuPartNumber"] for item in licences.json().get("value", [])] if licences.ok else []
        has_copilot = any("COPILOT" in sku.upper() and "365" in sku for sku in skus)
        results.append(_result(
            "Microsoft 365 Copilot licence (for the Mail and Calendar agents)", has_copilot,
            "Found" if has_copilot else
            "Not found: you can do everything else; watch the facilitator for the Mail/Calendar steps",
            blocking=False,
        ))
    except Exception as error:  # Surfaced as a warning rather than a crash.
        results.append(_result("Microsoft 365 Copilot licence", False, f"Couldn't check: {type(error).__name__}", blocking=False))

    return results


def assert_ready(results: list[dict[str, Any]]) -> None:
    blocking = [r["check"] for r in results if not r["ok"]]
    if blocking:
        raise RuntimeError("Fix these prerequisites first: " + "; ".join(blocking))
