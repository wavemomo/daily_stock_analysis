"""Low-risk system configuration endpoints for authorized miniapp administrators."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import (
    get_runtime_scheduler_service,
    get_system_config_service,
    require_permission,
)
from api.v1.endpoints import system_config
from api.v1.schemas.system_config import (
    AgentBackendStatusResponse,
    GenerationBackendStatusResponse,
    SystemConfigResponse,
    SystemConfigSchemaResponse,
    SetupStatusResponse,
    UpdateSystemConfigRequest,
    UpdateSystemConfigResponse,
    ValidateSystemConfigRequest,
    ValidateSystemConfigResponse,
)
from src.services.runtime_scheduler import RuntimeSchedulerService
from src.services.system_config_service import SystemConfigService
from src.services.wechat_miniapp_auth_service import MiniappPrincipal

router = APIRouter()


def _mask_sensitive_values(payload: SystemConfigResponse) -> SystemConfigResponse:
    """Mask every sensitive field before returning config to a miniapp client."""
    data = payload.model_dump(by_alias=True)
    mask_token = data["mask_token"]
    for item in data["items"]:
        schema = item.get("schema") or {}
        if schema.get("is_sensitive") and (item.get("raw_value_exists") or item.get("value")):
            item["value"] = mask_token
            item["is_masked"] = True
    return SystemConfigResponse.model_validate(data)


@router.get("/config", response_model=SystemConfigResponse, summary="Read masked system configuration")
def get_miniapp_system_config(
    _: MiniappPrincipal = Depends(require_permission("system.read")),
    service: SystemConfigService = Depends(get_system_config_service),
) -> SystemConfigResponse:
    payload = system_config.get_system_config(include_schema=True, service=service)
    return _mask_sensitive_values(payload)


@router.get("/config/schema", response_model=SystemConfigSchemaResponse, summary="Read system configuration schema")
def get_miniapp_system_config_schema(
    _: MiniappPrincipal = Depends(require_permission("system.read")),
    service: SystemConfigService = Depends(get_system_config_service),
) -> SystemConfigSchemaResponse:
    return system_config.get_system_config_schema(service=service)


@router.get("/config/setup/status", response_model=SetupStatusResponse, summary="Read setup readiness")
def get_miniapp_setup_status(
    _: MiniappPrincipal = Depends(require_permission("system.read")),
    service: SystemConfigService = Depends(get_system_config_service),
) -> SetupStatusResponse:
    return system_config.get_setup_status(service=service)


@router.get(
    "/config/generation-backends/status",
    response_model=GenerationBackendStatusResponse,
    summary="Read generation backend status",
)
def get_miniapp_generation_backend_status(
    _: MiniappPrincipal = Depends(require_permission("system.read")),
    service: SystemConfigService = Depends(get_system_config_service),
) -> GenerationBackendStatusResponse:
    return system_config.get_generation_backend_status(service=service)


@router.get(
    "/config/agent-backends/status",
    response_model=AgentBackendStatusResponse,
    summary="Read agent backend status",
)
def get_miniapp_agent_backend_status(
    _: MiniappPrincipal = Depends(require_permission("system.read")),
    service: SystemConfigService = Depends(get_system_config_service),
) -> AgentBackendStatusResponse:
    return system_config.get_agent_backend_status(service=service)


@router.get("/scheduler/status", summary="Read runtime scheduler status")
def get_miniapp_scheduler_status(
    _: MiniappPrincipal = Depends(require_permission("system.read")),
    scheduler: RuntimeSchedulerService = Depends(get_runtime_scheduler_service),
) -> dict:
    return scheduler.status()


@router.put("/config", response_model=UpdateSystemConfigResponse, summary="Update system configuration")
def update_miniapp_system_config(
    request: UpdateSystemConfigRequest,
    _: MiniappPrincipal = Depends(require_permission("system.manage")),
    service: SystemConfigService = Depends(get_system_config_service),
) -> UpdateSystemConfigResponse:
    return system_config.update_system_config(request=request, service=service)


@router.post(
    "/config/validate",
    response_model=ValidateSystemConfigResponse,
    summary="Validate system configuration",
)
def validate_miniapp_system_config(
    request: ValidateSystemConfigRequest,
    _: MiniappPrincipal = Depends(require_permission("system.manage")),
    service: SystemConfigService = Depends(get_system_config_service),
) -> ValidateSystemConfigResponse:
    return system_config.validate_system_config(request=request, service=service)
