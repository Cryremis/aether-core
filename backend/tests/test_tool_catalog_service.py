from app.services.tool_catalog_service import ToolCatalogService


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
