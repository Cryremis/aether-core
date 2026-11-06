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


@pytest.mark.parametrize("cache_source", ["aurora:on-demand", "aurora:on-demand:batch-1"])
def test_page_schema_discards_only_conflicting_entries_from_its_cache(cache_source, caplog):
    service = ToolCatalogService()
    session = AgentSession("page_schema_upgrade")
    old = descriptor(name="inference_wait_task_stage")
    new = descriptor(
        name=old["name"],
        input_schema={"type": "object", "properties": {"wait_seconds": {"type": "integer"}}},
    )
    other = descriptor(name="on_demand_only")
    page_source = "aurora:page"
    service.replace(session, [old], source_id=page_source, replace_all=False)
    service.replace(session, [old, other], source_id=cache_source, replace_all=False)
    with caplog.at_level(logging.WARNING):
        upgraded = service.replace(session, [new], source_id=page_source, replace_all=False)
        snapshot = service.snapshot(session)
        assert len(caplog.records) == 1
        replay = service.apply_operations(
            session, source_id=cache_source, upserts=[old], removals=[],
            expected_revision=upgraded.revision,
        )
    assert upgraded.updated == (old["name"],)
    assert upgraded.added == upgraded.removed == ()
    assert snapshot.descriptors_by_name[old["name"]]["input_schema"] == new["input_schema"]
    assert snapshot.sources_by_name[old["name"]] == (page_source,)
    assert snapshot.sources_by_name[other["name"]] == (cache_source,)
    assert [item["name"] for item in session.host_tool_collections[cache_source]] == [other["name"]]
    assert replay.changed is False
    assert replay.revision == upgraded.revision
    assert replay.fingerprint == upgraded.fingerprint
    assert len(caplog.records) == 2
    assert "input_schema" in caplog.text
    assert cache_source in caplog.text

    service.replace(session, [], source_id=page_source, replace_all=False)
    assert set(service.snapshot(session).descriptors_by_name) == {other["name"]}


@pytest.mark.parametrize("cache_source,extra", [
    ("another:on-demand", {}),
    ("aurora:on-demand", {"endpoint": "https://other.example/execute"}),
    ("aurora:on-demand", {"requires_auth": False}),
])
def test_schema_precedence_does_not_hide_unrelated_or_execution_conflicts(cache_source, extra):
    service = ToolCatalogService()
    session = AgentSession("strict_conflicts")
    service.replace(session, [descriptor()], source_id="aurora:page", replace_all=False)
    before = service.snapshot(session)
    with pytest.raises(ToolCatalogValidationError, match="input_schema"):
        service.replace(
            session, [descriptor(input_schema={"type": "object"}, **extra)],
            source_id=cache_source, replace_all=False,
        )
    assert service.snapshot(session) == before


def test_snapshot_reads_historical_page_and_stale_cache_without_mutating_revision():
    service = ToolCatalogService()
    session = AgentSession("historical_catalog")
    session.host_tool_collections = {
        "host:on-demand": [descriptor(input_schema={"type": "object"})],
        "host:page": [descriptor()],
    }
    session.host_tools_revision = 8
    session.host_tools_fingerprint = service._fingerprint(session.host_tool_collections)

    snapshot = service.snapshot(session)
    assert snapshot.revision == 8
    assert snapshot.sources_by_name["remote_exec"] == ("host:page",)
    assert "command" in snapshot.descriptors_by_name["remote_exec"]["input_schema"]["properties"]
    assert "host:on-demand" in session.host_tool_collections

    repaired = service.replace(session, [descriptor()], source_id="host:page", replace_all=False)
    assert repaired.revision == 9
    assert repaired.added == repaired.updated == repaired.removed == ()
    assert "host:on-demand" not in session.host_tool_collections
