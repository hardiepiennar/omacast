# Changelog

## Unreleased

## 0.1.4 — 2026-08-31

- Surface receiver-attributed Wi-Fi Direct negotiation failures immediately
  instead of waiting for the generic group timeout. Status 10 now explains
  that the display rejected push-button provisioning and may require PIN
  pairing; the valid sink remains visible and PIN provisioning stays tracked
  separately. The privileged monitor drains both streams continuously with
  fixed bounds, matches the exact control object and peer, and is carried by
  companion revision 79 without changing guard API 14 or engine API 2.
- Make automatic P2P channel selection reach the privileged supplicant broker,
  not only the FluxCast command. The closed launch plan now carries one
  canonical P2P frequency, with a regression proving that a 5745 MHz station
  reaches the guard as automatic (`0`) while the proven 2.4 GHz hint is kept.
- Keep Q and the Cancel button active while a selected receiver waits for
  progressive discovery to stop, cancel that queued handoff without starting
  a session, and restore keyboard focus across connection state changes. Stop
  requests are now recorded in bounded session history without making cleanup
  depend on history availability.
- Show objective sink metadata in receiver rows: WFD role, RTSP port,
  advertised throughput, manufacturer/model when supplied, and current signal
  quality. The row uses compact role/network/rate/signal icons and keeps the
  expanded definitions in its tooltip. Production patch 53 carries the bounded
  NetworkManager metadata in companion revision 77; serial numbers remain private.
- Publish changed receiver lists during one continuous Wi-Fi Direct discovery
  session, so nearby sinks appear immediately without restarting discovery or
  losing slower advertisements. Selecting a receiver cooperatively stops that
  scan before connection. Companion revision 78 carries production patch 54
  and requires engine contract API 2.
- Preserve NetworkManager's advertised WFD device role in discovery results,
  so the controller can identify genuine sinks without inferring capability
  from an RTSP port. Companion revision 76 carries production patch 52.

- Fix the companion `fluxcast --doctor` import cycle and cover the shipped
  diagnostic import order in a fresh Python interpreter.
- Rename the production WFD capture selector from the misleading
  `wf-recorder` value to `gpu-screen-recorder`, remove the obsolete optional
  dependency, enforce removal of every old selector in artifact and lifecycle
  gates, and require companion revision 63 / guard API 12.
- Remove the remaining alternate public WFD capture choices from companion
  revision 67; readiness now rejects an engine that still advertises those
  unshipped paths.
- Make FluxCast readiness respect the selected P2P backend, so the supported
  direct-supplicant client path no longer fails on an unused `dnsmasq` check.
- Stop forcing 5/6 GHz station frequencies into P2P group formation; retain
  the proven 2.4 GHz hint and let supplicant choose a legal channel otherwise.
- Remove the invalid WFD Device Name subelement from both source-advertisement
  implementations and require companion revision 65 / guard API 13.
- Let the explicit Restore action reclaim only fully inactive orphaned P2P
  clients after fresh administrator approval; ordinary startup never deletes
  an unowned interface, and every candidate is revalidated immediately before
  removal. Require companion revision 66 / guard API 14.
- Keep alternate NetworkManager group-owner support, persistent P2P pairing,
  and privileged-process cgroup decoupling as separately tracked architecture
  work instead of adding unvalidated fallbacks to this release.
- Bound decoded JSON, command output, wireless inventories, telemetry scans,
  session history, and privileged broker traffic at their production trust
  boundaries; reject coerced scalar types and incomplete liveness observations.
- Drain long-lived helper output throughout the session and terminate timed-out
  engine command process groups, including descendants that retain output
  pipes. Production patch 51 also removes the unused transport-dump analyzer
  from the installed engine. Companion revision 75 carries patches 49–51;
  guard API 14 and engine contract API 1 are unchanged.
- Close the controller's phase-specific runtime-state protocol and apply a
  shape budget before decoded controller data reaches QML.

## 0.1.3 — 2026-08-27

- Remove the supplicant broker's fixed eight-minute lifetime. Healthy casts
  now run until stopped while the renewable safety lease still recovers an
  abandoned session.
- Require companion revision 61 / guard API 11 so the plugin rejects packages
  containing the broker lifetime defect.
- Allow bounded controller discovery to publish session state on slower hosts
  before the panel cancels a stalled launch.

## 0.1.2 — 2026-08-27

- Reduce receiver connection time by waiting on owned network readiness and
  ending discovery as soon as the selected receiver appears.
- Remove the companion's dormant PyPI system installer, desktop/tray assets,
  and obsolete Chromecast/DLNA startup code.
- Bound internal command output, receiver-advertised port parsing, the live
  latency journal, and unanswered RTSP keepalive state.
- Keep the shipped companion WFD-only and add package-audit regressions for
  every removed or bounded surface.

## 0.1.1 — 2026-08-25

- Bound controller subprocess output, streaming QML collection, UI JSON
  responses, receiver/readiness/warning models, and runtime state/telemetry
  reads at their trust boundaries.
- Open bounded runtime state and current telemetry nonblocking so a FIFO
  replacement is rejected before it can stall descriptor validation.
- Remove the user-supplied media PID channel and privileged `renice` action.
  The existing user-owned transient service now applies `CPUWeight=10000` to
  its own supervised cast process tree without crossing a root boundary.
- Advance the companion package to revision 41 and guard API revision 7 so the
  controller rejects installations that retain the old privileged QoS path.
- Pin and validate the user-owned lease heartbeat through one bounded file
  descriptor so a same-UID special-file swap cannot block the root guard or its
  independent recovery process.
- Anchor the user-writable session markers below a root-owned session parent,
  and remove the unused privileged Stop verb, so a same-UID directory-symlink
  race cannot redirect root's directory ownership or mode changes.
- Record session-created P2P client interfaces in root-owned state and remove
  only those recorded devices during normal or recovery cleanup.
- Require API-9 independent recovery to validate the protected session and
  acknowledge readiness before any temporary network or D-Bus mutation.
- Require the controller to validate the helper's final bounded cleanup status
  instead of reporting cleanup complete from process exit alone.
- Render wireless and controller-derived labels as plain text and normalize the
  small QML models before they enter the shell UI.
- Promote the healthy-cast Nerd Mode view to a 16:9 marketplace preview while
  preserving the original receiver-backed panel capture as `nerd-mode.png`.

## 0.1.0 — 2026-08-22

- Companion revision 37 replaces the recurring cast password prompt with a
  package-owned Polkit action scoped to the exact guard path, `prepare` as the
  first argument, and the active local user. Guard API 4 additionally binds the
  requested UID to Polkit's authenticated caller.
- Clicking a receiver now starts casting immediately, matching Enter on the
  keyboard-selected receiver. N toggles Nerd Mode, Q stops an active cast,
  and the documented Super+C binding uses Omarchy's native panel toggle route.
- The upstream FluxCast source is pinned by its full 40-character commit ID.
- Receiver liveness now requires the session-owned P2P group to remain present.
  A TV-side disconnect returns the supervised session to idle after a
  three-second grace instead of trusting a stale RTSP socket and showing a
  false streaming state.
- N now opens Nerd Mode throughout connection as well as streaming, and Q
  cancels from the first busy connection state instead of waiting for media.
- The single Safe profile is now the receiver-accepted 1280×720 at 60 fps and
  7 Mbps Matroska path. A controlled Fire TV run was subjectively accepted with
  zero reported dropped/duplicated frames or radio retries in sampled telemetry.
- Nerd Mode keeps every existing signal but uses compact, scannable values and
  a health flag count instead of expanding raw issue strings. Each value uses
  signal-specific green, amber, or red thresholds for at-a-glance health.
- Nerd Mode's final layout uses a two-column metric grid and hides unavailable
  deep-probe cards. Panel launches snapshot the selected receiver and surface
  launcher stderr instead of silently returning to idle.
- Live discovery offers only the Fire TV receiver class validated for 0.1.0;
  generic WFD advertisements remain unsupported rather than appearing usable.
- Revision 34 consolidates the release on the receiver-proven full-display
  path. The experimental portal/window command surface, portal picker,
  GStreamer-only dependencies, and in-panel screenshot preview are removed.
  Their revision-31 through revision-33 findings remain in the research log.
- Super+C and the bar icon now open the same keyboard-first desktop workflow;
  the Source row identifies the output and one Enter starts the selected TV.
- The shipped FluxCast compatibility series contains only the accepted 20-patch
  display baseline while retaining encoded-frame proof before green streaming.
  Portal-only and rejected handoff patches remain research and are not shipped.
- Once RTSP connects, the single display capture path now has a focused
  30-second media-start deadline instead of the portal picker's long allowance.
- First Omacast marketplace release candidate.
- Native Omarchy bar panel with Super+C summon workflow.
- Automatic nearby-display discovery and receiver selection by name.
- Receiver discovery excludes Wi-Fi Direct devices that explicitly advertise
  no Wi-Fi Display information, so nearby printers are not offered as TVs, and
  puts the validated Fire TV receiver class before generic WFD labels.
- A stationary pointer can no longer move the keyboard destination cursor as
  the panel opens; arrow keys and Enter always act on the visibly highlighted
  receiver, while mouse selection remains click-based.
- Compact Bluetooth/Agents-style interface with a dedicated cast glyph and
  only the actions that drive the supported workflow.
- Orange connecting, green streaming, and red recovery icon states.
- Receiver scanning now shares the orange busy state, and tooltips distinguish
  discovery, administrator approval, receiver connection, and restoration.
- Cast supervision moved into a collectable user service so shell reloads do
  not own the media session.
- Failed authorization and lost-session ownership now lead to a contextual
  Restore action instead of trapping the panel behind Stop.
- The orange start-pending state bridges the systemd/status publication race,
  keeps duplicate actions disabled, and exposes immediate cancellation even
  before the detached service publishes session state. Its watchdog now
  cancels failed launches instead of merely unlocking the panel.
- Cancellation is valid from the first `checking` transition, and a service
  stopped before state publication performs a post-stop stale-state sweep so
  late startup cannot resurrect a dead cast in the panel.
- Stop and authorization cancellation now use the unprivileged session marker;
  only starting a real cast requires administrator approval.
- Guarded Fire TV mirror sessions with deterministic cleanup.
- Live capture, mux, transport, packet-timing, radio, and health telemetry.
- Volatile per-session telemetry is removed after normal completion and stale
  recovery; durable telemetry history is pruned with the same 50-session bound
  as event history.
- Integrated session elapsed time and bounded 50-session diagnostic history.
- Wayland idle and logind sleep inhibitors scoped exactly to the active cast;
  normal Stop and forced owner death release both automatically.
- Legacy hardware experiments moved under `scripts/lab/` so production and
  marketplace paths are unambiguous.
- Bootstrap and Arch packaging now consume one authoritative 22-patch series;
  a clean pinned-base reconstruction applied the complete series successfully.
- Receiver-validated 720p30 H.264/AAC profile with GPU Screen Recorder capture.
- Compatibility entry point retained as `omarchy-cast`.
- First-run readiness now gates automatic scanning on the complete supported
  engine/helper/host path and distinguishes companion setup from transient
  Wi-Fi, display, or audio availability.
- A contextual setup state replaces the receiver controls when the companion
  is missing; one action copies `makepkg -si` and opens a visible terminal.
- Companion package revision 25 promotes every required Miracast runtime tool
  from optional guidance to declared package dependencies.
- Closed panels now poll controller state with a lightweight heartbeat, so the
  bar icon changes state without requiring the user to click it first.
- P2P group formation has a dedicated 45-second timeout and the complete
  connection stage fails actionably after 75 seconds instead of inheriting the
  requested cast lifetime.
- Normal hardware sessions default to Cast Until Stopped. Companion package
  revision 26 replaces the fixed 30-minute privileged ceiling with a renewable
  60-second safety lease and an independent missed-heartbeat cleanup path.
- The networking helper safely creates a missing volatile
  `/run/systemd/network` directory after boot and restores the exact prior
  systemd-networkd service/socket state during cleanup.
- Companion patch 23 adds an explicit, default-off FLV handoff candidate for
  controlled GSR cadence testing while retaining the receiver-proven Matroska
  handoff as the normal path.
- Companion package revision 27 publishes a machine-readable guard API revision.
  Readiness now rejects an old engine or helper contract and shows the normal
  companion-update action before a mismatched marketplace UI can start a cast.
- The everyday live panel now keeps only display, session, quality, and health
  visible. Optional Nerd Mode reveals the full measured signal path, labels
  unavailable probes honestly, and never turns on deep packet tracing.
- Recovery now uses a warning glyph as well as urgent color, while state-aware
  tooltips distinguish readiness, setup, connection, casting, cleanup, and
  recovery without requiring the panel to be opened.
- Authorization cancellation, helper setup, DHCP, P2P, receiver negotiation,
  receiver timeout, capture, and otherwise-unclassified engine exits now have
  stable diagnostic codes. The controller preserves those codes in live state
  and bounded session history instead of collapsing them into one transport
  failure.
- A direct-supplicant group timeout is classified as P2P negotiation failure
  even when earlier engine diagnostics mention the later DHCP stage.
- Release artifacts now have a repeatable no-root audit for checksum, safe
  archive paths, runtime dependencies, executable permissions, guard API
  compatibility, packaged engine flags, and pacman archive integrity.
- A disposable fakeroot/pacman lifecycle test now proves that a prior companion
  installs intact, revision 27 upgrades it in place with zero altered files,
  exposes the new helper contract, and removes without orphaned package files.
- Superseded revision-31 experiment: active-window casting requested a typed
  window from the private desktop portal and failed closed if the picker
  returned a monitor, virtual output, or untyped source. Picker guidance used its
  Windows tab.
- Superseded revision-33 experiment: GStreamer's PipeWire reader replaced GPU
  Screen Recorder's incompatible portal SHM path and fed the receiver-proven FFmpeg
  VAAPI Safe pipeline. Display casting remains on the accepted GSR path.
- Connecting state now remains orange until FFmpeg proves an encoded video
  frame; an RTSP socket alone can no longer turn the bar icon green. Portal
  cancellation and selection timeout have distinct recovery guidance.
- The compatibility series is now 25 patches. Revision 33 passed its 101 engine
  tests, exact-clean artifact audit, fresh lifecycle, live revision-32 upgrade,
  package-integrity check, and Omarchy plugin validation.
- Marketplace metadata now uses the more discoverable Hardware + bar/media/
  quickshell combination, and the current upstream static baseline reports no
  findings while retaining the expected explicit-review capabilities.
- Marketplace and README copy now lead with the authentic privacy-safe panel
  preview and clearly distinguish the calm cast flow, optional Nerd Mode, and
  end-to-end recovery behavior.
- Companion revision 28 removes the household receiver's personal label and
  radio address from public research/test artifacts; retained lab launchers now
  require explicit receiver, Wi-Fi interface, and monitor inputs instead of
  embedding this workstation's values.
- Release CI now installs the candidate with pacman in a disposable root,
  verifies every packaged file and the helper API, then proves complete removal
  before artifact upload or provenance attestation.
- Read-only host diagnostics now expose the desktop ScreenCast portal version,
  standard source and cursor masks, and honest picker-dependent region status.
  The probe only reads D-Bus properties and never opens the source picker.
- Companion revision 29 extends independent missed-heartbeat recovery to the
  fixed allowlist of volatile session telemetry files, including the media QoS
  marker, then removes the empty session telemetry directory.
- Removed the old config surface and every experimental display/quality choice
  from production planning, simulation, probes, state validation, and guarded
  execution. Omacast now has one consistent mirror/Safe contract end to end.
- Companion revision 30 replaces ambiguous shell control-flow expressions,
  including a prepare-failure path that could incorrectly fall through to
  `stop`, and release CI now ShellChecks every production shell surface.
- Superseded revision-31 experiment: Super+C opened a private, contextual
  active-window prompt. Nearby receivers used an Omarchy-style keyboard cursor: ↑/↓ chose and one Enter
  starts the selected destination; mouse users must explicitly choose a TV
  before the Cast action appears.
- Super+C routes through Omarchy's stable shell-level summon API, so plugin
  upgrades cannot leave it calling a stale per-widget IPC handler. Clicking the
  bar icon remains the explicit whole-display route.
- Companion revision 31 adds an explicit desktop-portal source to the measured
  GPU Screen Recorder pipeline. Window selection retains Safe 720p30, audio,
  pacing, supervision, and cleanup, and remains gated on final receiver tests.
- Companion revision 32 accepts a safely pre-existing systemd-networkd service
  (including socket activation after a prior cast), reloads only the session
  configuration, and restores the exact recorded unit states. A root-owned
  helper exit can no longer mask its actionable setup error with `EPERM` during
  unprivileged cleanup.
