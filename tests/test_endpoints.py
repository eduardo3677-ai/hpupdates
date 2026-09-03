import pytest
from typer.testing import CliRunner

from hpupdates.cli import app
from hpupdates.infrastructure.endpoints import (
    EndpointPolicyError,
    endpoint_inventory,
    require_operational_endpoint,
)


def test_only_public_catalog_and_download_endpoints_are_operational() -> None:
    endpoints = endpoint_inventory()
    operational = {item.name for item in endpoints if item.operational}

    assert operational == {
        "hpia_catalog",
        "hpia_knowledge_base",
        "hpia_product_catalog",
        "softpaq_primary",
        "softpaq_extended",
    }
    assert all(item.url.startswith("https://") for item in endpoints if item.operational)


def test_regional_telemetry_endpoint_is_documented_but_blocked() -> None:
    endpoint = next(item for item in endpoint_inventory() if item.name == "analytics_us_west_2")

    assert endpoint.region == "us-west-2"
    assert endpoint.environment == "production"
    assert endpoint.category == "telemetry"
    assert endpoint.operational is False
    with pytest.raises(EndpointPolicyError, match="not authorized"):
        require_operational_endpoint(endpoint.name)


def test_nonproduction_and_internal_endpoints_are_never_operational() -> None:
    blocked = {
        item.name: item
        for item in endpoint_inventory()
        if item.environment
        in {"development", "testing", "staging", "integration", "sandbox", "internal"}
    }

    assert {
        "methone_integration",
        "methone_sandbox",
        "identity_staging",
        "virtual_agent_dev",
        "houston_internal",
    } <= set(blocked)
    assert all(not item.operational for item in blocked.values())
    assert all(item.credentials == "not-included" for item in blocked.values())


def test_unknown_endpoint_name_fails_closed() -> None:
    with pytest.raises(EndpointPolicyError, match="unknown"):
        require_operational_endpoint("arbitrary")



