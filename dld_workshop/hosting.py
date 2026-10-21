from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointProtocol,
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
    RaiConfig,
)
from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.authorization.models import RoleAssignmentCreateParameters

from .agents import RULES_AGENT
from .environment import WorkshopSettings


HOSTED_AGENT = "DLD-Rental-Manager"
ACR_READER_ROLE = "b93aa761-3e63-49ed-ac28-beffa264f7ac"
ACR_PULL_ROLE = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
BLOB_READER_ROLE = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
FOUNDRY_AGENT_CONSUMER_ROLE = "eed3b665-ab3a-47b6-8f48-c9382fb1dad6"
COGNITIVE_SERVICES_USER_ROLE = "a97b65f3-24c7-4388-baec-2e87135dc908"


def _run(*arguments: str) -> str:
    executable = shutil.which(arguments[0])
    if not executable:
        raise FileNotFoundError(f"{arguments[0]} is not installed or not on PATH.")
    result = subprocess.run(
        [executable, *arguments[1:]],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_container(
    settings: WorkshopSettings,
    source_directory: str | Path,
    image_tag: str = "v1",
) -> str:
    registry = settings.container_registry_name or f"dldacr{settings.suffix.lower()}"
    settings.container_registry_name = registry
    _run(
        "az",
        "acr",
        "create",
        "--resource-group",
        settings.resource_group,
        "--name",
        registry,
        "--sku",
        "Basic",
        "--admin-enabled",
        "false",
        "--output",
        "none",
    )
    _run(
        "az",
        "acr",
        "config",
        "authentication-as-arm",
        "update",
        "--registry",
        registry,
        "--status",
        "enabled",
    )
    image = f"dld-rental-manager:{image_tag}"
    _run(
        "az",
        "acr",
        "build",
        "--registry",
        registry,
        "--image",
        image,
        "--no-logs",
        str(source_directory),
    )
    return f"{registry}.azurecr.io/{image}"


def _assign_role(
    settings: WorkshopSettings,
    credential: Any,
    *,
    principal_id: str,
    role_id: str,
    scope: str,
) -> None:
    client = AuthorizationManagementClient(credential, settings.subscription_id)
    assignment_name = str(uuid5(NAMESPACE_URL, f"{scope}|{principal_id}|{role_id}"))
    try:
        client.role_assignments.create(
            scope,
            assignment_name,
            RoleAssignmentCreateParameters(
                role_definition_id=(
                    f"/subscriptions/{settings.subscription_id}"
                    f"/providers/Microsoft.Authorization/roleDefinitions/{role_id}"
                ),
                principal_id=principal_id,
                principal_type="ServicePrincipal",
            ),
        )
    except ResourceExistsError:
        pass


def deploy_hosted_agent(
    settings: WorkshopSettings,
    credential: Any,
    *,
    image: str,
    guardrail: bool = True,
    wait_for_rbac_seconds: int = 120,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    registry_name = image.split(".", 1)[0]
    registry_id = (
        f"/subscriptions/{settings.subscription_id}/resourceGroups/{settings.resource_group}"
        f"/providers/Microsoft.ContainerRegistry/registries/{registry_name}"
    )
    for role_id in (ACR_READER_ROLE, ACR_PULL_ROLE):
        try:
            _assign_role(
                settings,
                credential,
                principal_id=settings.project_principal_id,
                role_id=role_id,
                scope=registry_id,
            )
        except HttpResponseError as error:
            if error.status_code != 409:
                raise
    if wait_for_rbac_seconds:
        print(f"Waiting {wait_for_rbac_seconds}s for registry permissions to propagate...")
        time.sleep(wait_for_rbac_seconds)

    project = AIProjectClient(settings.project_endpoint, credential)
    definition_options = {
        "protocol_versions": [
            ProtocolVersionRecord(
                protocol=AgentEndpointProtocol.RESPONSES,
                version="2.0.0",
            )
        ],
        "cpu": "1",
        "memory": "2Gi",
        "container_configuration": ContainerConfiguration(image=image),
        "environment_variables": {
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": settings.orchestrator_model,
            "WORKSHOP_STORAGE_ACCOUNT": settings.storage_name,
            "RULES_AGENT": RULES_AGENT,
            "WORKSHOP_FOUNDRY_ACCOUNT": settings.foundry_account_name,
        },
    }
    if guardrail:
        if not settings.rai_policy_id:
            raise ValueError("The workshop RAI policy ID is missing from settings.")
        definition_options["rai_config"] = RaiConfig(
            rai_policy_name=settings.rai_policy_id
        )

    created = project.agents.create_version(
        agent_name=HOSTED_AGENT,
        definition=HostedAgentDefinition(**definition_options),
        description="DLD rental-services manager (Microsoft Agent Framework container, own identity).",
    )
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        version = project.agents.get_version(
            agent_name=HOSTED_AGENT,
            agent_version=str(created.version),
        )
        status = str(getattr(version.status, "value", version.status)).lower()
        print(f"Hosted agent {created.version}: {status}")
        if status == "active":
            details = project.agents.get(HOSTED_AGENT)
            identity = details.instance_identity
            if not identity:
                raise RuntimeError("Hosted agent became active without an instance identity.")
            allowed_container_scope = (
                f"{settings.storage_id}/blobServices/default/containers/"
                "rental-service-policies"
            )
            _assign_role(
                settings,
                credential,
                principal_id=identity.principal_id,
                role_id=BLOB_READER_ROLE,
                scope=allowed_container_scope,
            )
            # Specialist prompt agents are invoked through the project endpoint.
            _assign_role(
                settings,
                credential,
                principal_id=identity.principal_id,
                role_id=FOUNDRY_AGENT_CONSUMER_ROLE,
                scope=settings.project_id,
            )
            # Prompt Shields screening runs on the Foundry account under the agent's identity.
            _assign_role(
                settings,
                credential,
                principal_id=identity.principal_id,
                role_id=COGNITIVE_SERVICES_USER_ROLE,
                scope=settings.foundry_account_id,
            )
            return {
                "name": HOSTED_AGENT,
                "version": str(created.version),
                "status": status,
                "image": image,
                "image_sha256": hashlib.sha256(image.encode()).hexdigest(),
                "guardrail": guardrail,
                "rai_policy_id": settings.rai_policy_id if guardrail else None,
                "agent_identity_principal_id": identity.principal_id,
                "agent_identity_client_id": identity.client_id,
            }
        if status == "failed":
            raise RuntimeError(json.dumps(version.as_dict().get("error"), default=str))
        time.sleep(10)
    raise TimeoutError("Hosted agent did not become active before the timeout.")


def invoke_hosted_agent(
    settings: WorkshopSettings,
    credential: Any,
    message: str,
) -> str:
    project = AIProjectClient(settings.project_endpoint, credential)
    response = project.get_openai_client(agent_name=HOSTED_AGENT).responses.create(
        input=message
    )
    return response.output_text or ""
