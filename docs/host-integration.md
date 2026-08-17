<!-- docs/host-integration.md -->

# Host Integration Notes

This document is a maintainer note for AetherCore's host binding model. It is not the primary onboarding guide for host platform owners.

For real platform onboarding, register a platform in the AetherCore admin console and open that platform's built-in integration guide. The product guide contains the current frontend snippets, backend bind templates, authentication options, and optional host tool examples.

## Runtime Model

Host integration has two layers:

- `host bind`: the host backend binds the current user, conversation key, page context, and optional capabilities to an AetherCore session through `POST /api/v1/host/bind`.
- `embedded workbench`: the host frontend mounts the universal adapter and opens the AetherCore workbench with the embed token returned by bind.

During bind, a host can pass:

- `context`: current user, page, auth, and host-specific extras.
- `tools`: host-side callable capabilities. AetherCore exposes these to the agent runtime and calls the declared host endpoint when the model selects a tool.
- `skills`: host-provided domain instructions or workflow guidance.
- `apis`: host API metadata reserved for adapter and tooling expansion.

## Host Tools

Host tools are session-level descriptors, not uploaded host code. A descriptor includes the tool name, model-facing description, JSON input schema, and the host endpoint AetherCore should call.

The implementation schema lives in [backend/app/schemas/host.py](../backend/app/schemas/host.py). Tool listing and execution are handled in [backend/app/services/tool_service.py](../backend/app/services/tool_service.py).

### Dynamic Tool Catalog

Hosts that need progressive disclosure can update a session's tool catalog after the initial bind:

- `GET /api/v1/host/sessions/{session_id}/tools` returns the current automatic revision, fingerprint, tool names, and owning source IDs. Add `include_descriptors=true` only for diagnostics that need complete descriptors.
- `PUT /api/v1/host/sessions/{session_id}/tools` atomically replaces all tools owned by one `source_id`. Set `replace_all=true` only when intentionally replacing the complete host catalog.
- `PATCH /api/v1/host/sessions/{session_id}/tools` atomically applies source-owned upserts and removals.

All three endpoints use the same platform-secret authentication as host bind and verify that the target session belongs to that platform. Revisions and fingerprints are generated and persisted by AetherCore; host operators do not maintain them. `expected_revision` is an optional optimistic-concurrency guard for callers that need compare-and-swap behavior.

Tool ownership is source-aware. A page can own `host:page` while an on-demand loader owns `host:on-demand:devices`; replacing the page source does not remove the on-demand source. Multiple sources may own the same tool only when their complete descriptors are identical. A conflicting definition is rejected instead of choosing one nondeterministically.

For a host that refreshes page tools, bind with:

```json
{
  "tool_source_id": "host:page",
  "tool_update_mode": "replace_all_if_source_missing",
  "tool_refresh_policy": "round_boundary"
}
```

`replace_all_if_source_missing` migrates a legacy session once, then only replaces that source on later binds. `round_boundary` captures an immutable catalog for each model round: calls produced by that round execute against the same snapshot, while catalog updates become visible before the next internal model round. This allows an Agent to discover a capability, load tools, and use them within one user response without changing the tool list during an in-flight model call. Existing hosts remain compatible because omitted fields retain `replace_all` and `static_run` behavior.

## Documentation Ownership

- Root README: product value, deployer overview, and capability positioning.
- Built-in integration guide: copyable onboarding instructions for platform owners.
- This file: internal notes for maintainers changing host bind behavior.
