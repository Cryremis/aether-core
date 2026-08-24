# Universal Embed Adapter

This directory contains the browser-side adapter used to mount AetherCore inside a host product.

Most host platform owners should not use this file as their integration guide. After registering a platform in the AetherCore admin console, open the platform's built-in integration guide. That page generates copyable frontend and backend snippets with the correct `platform_key`, `host_secret`, deployment mode, and framework examples.

## Files

- `aethercore-embed.js`: the framework-agnostic browser loader served from `/api/v1/host/public/embed/aethercore-embed.js`.

## Assistant Preview

The universal adapter can show the latest user-visible assistant text beside the launcher. Reasoning, tool calls, and tool results are excluded at the Workbench event source. The preview replaces the previous text, is clamped to three lines, and opens the Workbench when clicked. Text received while the Workbench is open is retained and the latest unseen preview is shown when the drawer closes.

The capability is opt-in for existing hosts:

```js
window.mountAetherCore({
  // Existing bind options...
  assistantPreview: {
    enabled: true,
    proactive: {
      enabled: true,
      messages: [
        "快来试试，我可以帮你操作平台。",
        "有什么问题可以来问我。",
      ],
    },
  },
});
```

Proactive prompts first appear 30-60 seconds after the page opens. After a prompt is actually shown, the next eligible prompt time is randomized between one and four hours and persisted per platform user. A prompt appears at most once per browser-tab session. An open Workbench or active `input`, `textarea`, or `contenteditable` field defers the prompt instead of consuming it. These defaults can be overridden through `assistantPreview.proactive`.

Live assistant previews are independent from proactive prompt suppression. Public instance methods `showAssistantPreview(text)`, `hideAssistantPreview()`, and `destroy()` are available for host lifecycle integrations. `onAssistantPreview` observes normalized preview updates.

## Maintainer Notes

- Keep this adapter framework-neutral. Host products should configure it through `window.mountAetherCore(...)`, not by editing the adapter.
- Keep generated examples in `backend/app/services/platform_integration_service.py` aligned with any adapter API changes.
- Host tool examples and bind details belong in the built-in integration guide, because that is the surface platform owners use while onboarding.
