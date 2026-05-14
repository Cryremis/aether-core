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

## Documentation Ownership

- Root README: product value, deployer overview, and capability positioning.
- Built-in integration guide: copyable onboarding instructions for platform owners.
- This file: internal notes for maintainers changing host bind behavior.
