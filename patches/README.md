# FluxCast patch stack

Omacast uses FluxCast as its Miracast/Wi-Fi Display engine. The companion Arch
package starts from the immutable upstream commit recorded in `PKGBUILD`, then
applies a checked-in patch series. This keeps the exact engine reproducible and
reviewable without hiding the changes in a private fork.

## Production

`production/series` is the sole build authority. Its 21 ordered patches cover
five parts of the receiver-tested path:

- Wi-Fi Direct and WFD negotiation: patches 1–2 and 5.
- Hyprland, VAAPI, GPU Screen Recorder, and synchronized audio capture:
  patches 3–4, 12–13, and 22.
- Receiver-compatible muxing, RTP pacing, buffering, and transport timing:
  patches 6, 9–11, 14–16, and 18–21.
- Machine-readable live telemetry: patch 17.
- Selected-receiver authentication and single-client admission: patch 27.

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
