# Universal Embed Adapter

This directory contains the browser-side adapter used to mount AetherCore inside a host product.

Most host platform owners should not use this file as their integration guide. After registering a platform in the AetherCore admin console, open the platform's built-in integration guide. That page generates copyable frontend and backend snippets with the correct `platform_key`, `host_secret`, deployment mode, and framework examples.

## Files

- `aethercore-embed.js`: the framework-agnostic browser loader served from `/api/v1/host/public/embed/aethercore-embed.js`.

## Maintainer Notes

- Keep this adapter framework-neutral. Host products should configure it through `window.mountAetherCore(...)`, not by editing the adapter.
- Keep generated examples in `backend/app/services/platform_integration_service.py` aligned with any adapter API changes.
- Host tool examples and bind details belong in the built-in integration guide, because that is the surface platform owners use while onboarding.
