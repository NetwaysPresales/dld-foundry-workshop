from __future__ import annotations

from dataclasses import asdict, dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import requests
from azure.identity import AzureCliCredential, DeviceCodeCredential
from azure.mgmt.resource import ResourceManagementClient, SubscriptionClient


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_NAME = "dld-workshop"


def _apply_outputs(settings: "WorkshopSettings", outputs: dict[str, Any]) -> None:
    """Copy the template's outputs (camelCase) onto the matching settings fields (snake_case)."""
    for field, value in outputs.items():
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", field).lower()
        if hasattr(settings, snake):
            setattr(settings, snake, value["value"])


@dataclass
class WorkshopSettings:
    subscription_id: str
    resource_group: str
    suffix: str
    location: str = "swedencentral"
    search_location: str = "uaenorth"
    search_sku: str = "free"
    tenant_id: str | None = None
    user_object_id: str | None = None
    foundry_account_name: str | None = None
    foundry_account_id: str | None = None
    project_name: str | None = None
    project_id: str | None = None
    project_endpoint: str | None = None
    project_principal_id: str | None = None
    search_name: str | None = None
    search_id: str | None = None
    search_endpoint: str | None = None
    storage_name: str | None = None
    storage_id: str | None = None
    application_insights_name: str | None = None
    application_insights_connection_string: str | None = None
    log_analytics_id: str | None = None
    rai_policy_id: str | None = None
    # Work IQ connections each attendee creates once in the Foundry portal (they act as the signed-in user).
    mail_connection_name: str = "WorkIQMail"
    calendar_connection_name: str = "WorkIQCalendar"
    container_registry_name: str | None = None
    logic_app_name: str | None = None
    outlook_connection_name: str | None = None
    # Model Router loses tool results in the MAF tool-calling path (verified); keep a fixed model here.
    orchestrator_model: str = "gpt-5.4-mini"

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z0-9]{4,14}", self.suffix.lower()):
            raise ValueError("suffix must contain 4-14 lowercase letters or digits")
        if self.search_sku not in {"free", "basic"}:
            raise ValueError("search_sku must be 'free' or 'basic'")
        if not self.resource_group.startswith("rg-"):
            raise ValueError("Use a dedicated resource group whose name starts with 'rg-'.")

    def save(self, path: str | Path = "workshop-settings.json") -> Path:
        destination = Path(path)
        destination.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: str | Path = "workshop-settings.json") -> "WorkshopSettings":
        known = {f for f in cls.__dataclass_fields__}
        values = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in values.items() if k in known})


class WorkshopEnvironment:
    """Idempotently provisions and removes one attendee-owned workshop environment."""

    PROVIDERS = (
        "Microsoft.CognitiveServices",
        "Microsoft.Search",
        "Microsoft.Storage",
        "Microsoft.Insights",
        "Microsoft.OperationalInsights",
        "Microsoft.ContainerRegistry",
        "Microsoft.Logic",
        "Microsoft.Web",
    )

    def __init__(self, credential: Any, settings: WorkshopSettings) -> None:
        settings.validate()
        self.credential = credential
        self.settings = settings
        self.resources = ResourceManagementClient(credential, settings.subscription_id)

    @staticmethod
    def credential(*, tenant_id: str | None = None, prefer_cli: bool = False):
        if prefer_cli:
            return AzureCliCredential(tenant_id=tenant_id)
        return DeviceCodeCredential(tenant_id=tenant_id)

    @staticmethod
    def subscriptions(credential: Any) -> list[dict[str, str]]:
        return [
            {
                "id": item.subscription_id,
                "name": item.display_name,
                "tenant_id": item.tenant_id,
                "state": str(item.state),
            }
            for item in SubscriptionClient(credential).subscriptions.list()
        ]

    @staticmethod
    def signed_in_identity(credential: Any) -> dict[str, str | None]:
        token = credential.get_token("https://management.azure.com/.default").token
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return {
            "object_id": claims.get("oid"),
            "tenant_id": claims.get("tid"),
            "user_name": claims.get("preferred_username") or claims.get("upn"),
        }

    @classmethod
    def automatic_settings(cls, credential: Any, subscription_id: str | None = None) -> WorkshopSettings:
        """Work out every setting from the signed-in user, so attendees type nothing.

        WORKSHOP_* environment variables override any value (used by facilitators and tests).
        """
        identity = cls.signed_in_identity(credential)
        subscription_id = subscription_id or os.getenv("WORKSHOP_SUBSCRIPTION_ID")
        if not subscription_id:
            enabled = [s for s in cls.subscriptions(credential) if s["state"].lower().endswith("enabled")]
            if len(enabled) != 1:
                names = ", ".join(f"{s['name']} ({s['id']})" for s in enabled) or "none"
                raise ValueError(
                    f"You have {len(enabled)} enabled subscriptions: {names}. "
                    "Set SUBSCRIPTION_ID in the cell to the one to use."
                )
            subscription_id = enabled[0]["id"]

        # Stable per person and subscription, so re-running finds the same resources.
        user = re.sub(r"[^a-z0-9]", "", (identity["user_name"] or "attendee").split("@")[0].lower())[:8] or "attendee"
        digest = hashlib.sha1(f"{identity['object_id']}|{subscription_id}".encode()).hexdigest()[:4]
        suffix = os.getenv("WORKSHOP_SUFFIX") or f"{user}{digest}"
        settings = WorkshopSettings(
            subscription_id=subscription_id,
            resource_group=os.getenv("WORKSHOP_RESOURCE_GROUP") or f"rg-dld-foundry-{suffix}",
            suffix=suffix,
            search_location=os.getenv("WORKSHOP_SEARCH_LOCATION") or "uaenorth",
            tenant_id=identity["tenant_id"],
            user_object_id=identity["object_id"],
        )
        settings.search_sku = os.getenv("WORKSHOP_SEARCH_SKU") or (
            "free" if cls.free_search_slot_available(credential, settings) else "basic"
        )
        settings.validate()
        return settings

    @classmethod
    def discover(cls, credential: Any, subscription_id: str | None = None) -> WorkshopSettings:
        """Rebuild the full settings of an existing environment from the sign-in and its Azure deployment.

        Notebooks 01-07 call this, so nothing has to be saved or carried between notebooks.
        """
        settings = cls.automatic_settings(credential, subscription_id)
        resources = ResourceManagementClient(credential, settings.subscription_id)
        try:
            deployment = resources.deployments.get(settings.resource_group, DEPLOYMENT_NAME)
        except Exception as error:
            raise RuntimeError(
                f"No workshop environment found in {settings.resource_group}. Run notebook 00 first, "
                "signed in with the same work account."
            ) from error
        _apply_outputs(settings, deployment.properties.outputs or {})
        settings.container_registry_name = f"dldacr{settings.suffix.lower()}"
        return settings

    @staticmethod
    def free_search_slot_available(credential: Any, settings: WorkshopSettings) -> bool:
        """Each subscription may have one Free Search service; reuse of our own one is fine."""
        token = credential.get_token("https://management.azure.com/.default").token
        response = requests.get(
            f"https://management.azure.com/subscriptions/{settings.subscription_id}"
            "/providers/Microsoft.Search/searchServices?api-version=2025-05-01",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        response.raise_for_status()
        own = f"dldsearch{settings.suffix.lower()}"
        return not any(s["sku"]["name"] == "free" and s["name"] != own for s in response.json().get("value", []))

    def register_providers(self) -> None:
        for namespace in self.PROVIDERS:
            state = self.resources.providers.get(namespace).registration_state
            if state != "Registered":
                print(f"Registering {namespace}...")
                self.resources.providers.register(namespace)

    def check_permissions(self) -> dict[str, bool]:
        """Report whether the signed-in identity can create resources and assign roles."""
        token = self.credential.get_token("https://management.azure.com/.default").token
        response = requests.get(
            f"https://management.azure.com/subscriptions/{self.settings.subscription_id}"
            "/providers/Microsoft.Authorization/permissions?api-version=2022-04-01",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        response.raise_for_status()
        allowed: set[str] = set()
        denied: set[str] = set()
        for permission in response.json().get("value", []):
            allowed.update(action.lower() for action in permission.get("actions", []))
            denied.update(action.lower() for action in permission.get("notActions", []))

        def can(action: str) -> bool:
            action = action.lower()
            granted = any(
                rule == "*" or action == rule or (rule.endswith("/*") and action.startswith(rule[:-1]))
                for rule in allowed
            )
            blocked = any(
                action == rule or (rule.endswith("/*") and action.startswith(rule[:-1]))
                for rule in denied
            )
            return granted and not blocked

        return {
            "create resource groups and resources": can(
                "Microsoft.Resources/subscriptions/resourceGroups/write"
            ),
            "assign roles": can("Microsoft.Authorization/roleAssignments/write"),
        }

    def provision(self, *, template_path: str | Path | None = None) -> WorkshopSettings:
        template_path = Path(template_path or ROOT / "infra" / "main.json")
        if not template_path.exists():
            raise FileNotFoundError(f"Compiled ARM template not found: {template_path}")

        preflight = self.check_permissions()
        missing = [name for name, allowed in preflight.items() if not allowed]
        if missing:
            raise PermissionError(
                "Your identity lacks required permissions on this subscription: "
                f"{', '.join(missing)}. Ask for Owner, or Contributor plus "
                "Role Based Access Control Administrator."
            )
        self.register_providers()

        self.resources.resource_groups.create_or_update(
            self.settings.resource_group,
            {
                "location": self.settings.location,
                "tags": {
                    "workload": "dld-property-service-workshop",
                    "purpose": "training",
                    "cleanup": "prompted",
                },
            },
        )

        from azure.mgmt.resource.resources.models import (
            Deployment,
            DeploymentMode,
            DeploymentProperties,
        )

        user_object_id = self.settings.user_object_id
        if not user_object_id:
            raise ValueError("Set settings.user_object_id before provisioning.")

        template = json.loads(template_path.read_text(encoding="utf-8"))
        parameters = {
            "suffix": {"value": self.settings.suffix.lower()},
            "location": {"value": self.settings.location},
            "searchLocation": {"value": self.settings.search_location},
            "searchSku": {"value": self.settings.search_sku},
            "userObjectId": {"value": user_object_id},
        }
        deployment = Deployment(
            properties=DeploymentProperties(
                mode=DeploymentMode.INCREMENTAL,
                template=template,
                parameters=parameters,
            )
        )
        result = self.resources.deployments.begin_create_or_update(
            self.settings.resource_group,
            DEPLOYMENT_NAME,
            deployment,
        ).result()
        _apply_outputs(self.settings, result.properties.outputs or {})
        self.settings.container_registry_name = f"dldacr{self.settings.suffix.lower()}"
        return self.settings

    def delete(self, confirmation: str) -> None:
        if confirmation != self.settings.resource_group:
            raise ValueError("Confirmation must exactly match the resource-group name.")
        self.resources.resource_groups.begin_delete(self.settings.resource_group).result()
