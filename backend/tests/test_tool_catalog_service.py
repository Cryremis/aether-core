import copy
import logging

import pytest

from app.services.session_types import AgentSession
from app.services.tool_catalog_service import ToolCatalogService, ToolCatalogValidationError


def test_compose_ignores_missing_and_empty_descriptor_defaults():
    service = ToolCatalogService()
    base = {
        "name": "host_tool",
        "description": "host tool",
        "input_schema": {"type": "object"},
    }
    collections = {
        "host:first": [dict(base)],
        "host:second": [{**base, "headers": {}, "tags": []}],
    }

    descriptors, sources = service._compose(collections)

    assert len(descriptors) == 1
    assert descriptors[0]["headers"] == {}
    assert descriptors[0]["tags"] == []
    assert sources["host_tool"] == ("host:first", "host:second")


def test_fingerprint_ignores_missing_and_empty_descriptor_defaults():
    service = ToolCatalogService()
    base = {
        "name": "host_tool",
        "description": "host tool",
        "input_schema": {"type": "object"},
    }
    missing = {"host:first": [dict(base)]}
    empty = {"host:first": [{**base, "headers": {}, "tags": []}]}

    assert service._fingerprint(missing) == service._fingerprint(empty)


def descriptor(**overrides):
    return {
        "name": "remote_exec", "description": "Old description",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
        "endpoint": "http://host/run", **overrides,
    }


def test_metadata_differences_keep_live_page_without_rejecting_catalog(caplog):
    service = ToolCatalogService()
    session = AgentSession("metadata_sources")
    service.replace(session, [descriptor(tags=["old"])], source_id="host:legacy", replace_all=True)
    page = descriptor(description="Current instructions", tags=["current"])
    with caplog.at_level(logging.WARNING):
        service.replace(session, [page], source_id="platform:page", replace_all=False)
        warning_count = len(caplog.records)
        for _ in range(3):
            snapshot = service.snapshot(session)
        service.replace(session, [page], source_id="platform:page", replace_all=False)
    assert warning_count == 1
    assert len(caplog.records) == warning_count
    assert snapshot.descriptors_by_name["remote_exec"]["description"] == "Current instructions"
    assert snapshot.sources_by_name["remote_exec"] == ("platform:page", "host:legacy")
    reverse, _ = service._compose({"platform:page": [page], "host:legacy": [descriptor()]})
    assert reverse[0]["description"] == "Current instructions"


@pytest.mark.parametrize("change,field", [
    ({"endpoint": "http://different/run"}, "endpoint"),
    ({"method": "GET"}, "method"),
    ({"requires_auth": False}, "requires_auth"),
    ({"auth_inject": False}, "auth_inject"),
    ({"headers": {"Authorization": "private"}}, "headers"),
    ({"input_schema": {"type": "object", "required": ["command"]}}, "input_schema"),
])
def test_execution_conflicts_are_diagnostic_and_atomic(change, field):
    service = ToolCatalogService()
    session = AgentSession("execution_conflict")
    service.replace(session, [descriptor()], source_id="host:legacy", replace_all=True)
    before = copy.deepcopy(session.host_tool_collections), session.host_tools_revision
    with pytest.raises(ToolCatalogValidationError, match=field) as raised:
        service.replace(session, [descriptor(**change)], source_id="platform:page", replace_all=False)
    assert "private" not in str(raised.value)
    assert (session.host_tool_collections, session.host_tools_revision) == before


def test_explicit_execution_defaults_match_omitted_defaults():
    service = ToolCatalogService()
    result, _ = service._compose({
        "first": [descriptor()],
        "second": [descriptor(method="post", auth_inject=True, requires_auth=True)],
    })
    assert len(result) == 1
