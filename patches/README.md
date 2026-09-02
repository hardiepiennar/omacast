# FluxCast patch stack

Omacast uses FluxCast as its Miracast/Wi-Fi Display engine. The companion Arch
package starts from the immutable upstream commit recorded in `PKGBUILD`, then
applies a checked-in patch series. This keeps the exact engine reproducible and
reviewable without hiding the changes in a private fork.

## Production

`production/series` is the sole build authority. Its 52 ordered patches cover
eight parts of the receiver-tested path:

- Wi-Fi Direct and WFD negotiation: patches 1–2 and 5.
- Hyprland, VAAPI, GPU Screen Recorder, and synchronized audio capture:
  patches 3–4, 12–13, and 22.
- Receiver-compatible muxing, RTP pacing, buffering, and transport timing:
  patches 6, 9–11, 14–16, and 18–21.
- Machine-readable live telemetry: patch 17.
- Selected-receiver authentication, single-client admission, and bounded RTSP
  parsing/connection handling: patches 27–28.
- Bounded latest-record FFmpeg progress for the supported desktop path: patch
  29.
- WFD-only package and CLI scope, excluding unused tray, Chromecast, DLNA, and
  LAN-server modules: patch 35.
- Removal of the unused unauthenticated WFD input-back-channel listener and
  local input injector: patch 36.
- Faster selected-receiver discovery and removal of the legacy PyPI installer,
  desktop/tray assets, and obsolete protocol startup path: patches 37–38.
- Memory-bounded capture of internal command output: patch 39.
- Strict receiver-advertised RTP/client port validation: patch 40.
- Bounded long-session latency journaling and keepalive state: patch 41.
- Import-order-safe companion diagnostics: patch 42.
- An honest GPU Screen Recorder capture selector and internal method name:
  patch 43.
- Backend-aware readiness that requires dnsmasq only for NetworkManager's
  group-owner/DHCP-server path: patch 44.
- A specification-valid WFD source advertisement containing only the defined
  Device Information subelement: patch 45.
- A single honest public WFD capture selector for the supported GPU Screen
  Recorder path: patch 46.
- A controller-issued, closed engine execution contract and bounded diagnostic
  inputs: patches 47–51.
- Complete NetworkManager WFD sink roles and bounded objective receiver
  metadata: patches 52–53.
- Progressive snapshots from one cancellable discovery session: patch 54.
- A bounded inherited descriptor for an explicitly entered receiver PIN and a
  closed engine capability for that path: patch 55.
- Immediate bounded lookup of the selected receiver for active RTSP, while a
  confirmed passive connection retains priority: patch 56.
- Unambiguous parsing of Samsung's identical two-header Content-Length form,
  while conflicting or excessive duplicates remain rejected: patch 57.
- Android-compatible empty SETUP-response framing for older receivers: patch
  58.

The patches are intentionally atomic. Package builds apply them with `git am`,
run FluxCast's tests, and fail if the pinned base or series no longer applies
cleanly. Omacast never patches an installed engine at runtime.

## Research

`research/` preserves six experiments that informed the final design but are
not applied by `production/series`:

- Patches 7–8: an early portal/OpenH264 capture route.
- Patch 23: a low-burst FLV handoff that performed worse on the receiver.
- Patches 24–25: portal window-capture experiments removed from the product.
- Patch 26: an MPEG-TS handoff that remained visibly stuttery on the receiver.

The gaps in production numbering are therefore deliberate. These files remain
as engineering evidence and regression context; their presence does not expose
the rejected features in the package, controller, or UI.
