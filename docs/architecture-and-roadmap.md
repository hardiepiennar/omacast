# Omacast architecture and roadmap

Status: canonical plan, updated 2026-08-26

This document turns the Phase 1 Miracast research into a path to a dependable
Omarchy plugin. It is the starting point for implementation and handoff. The
detailed experiment log remains in `docs/research-log.md`.

## 1. Product goal

Build an installable Omarchy 4 plugin that lets a Hyprland user discover a
nearby Miracast/WFD display, start a mirrored desktop session with audio, see
useful connection state, and stop the session without leaving networking or
Hyprland altered. Omacast has one production mode/quality contract: a Safe
720p60 stream using the selected Hyprland display as the receiver-proven source.
Portal/window casting and the local screenshot preview were evaluated in
revisions 31–33, then superseded on 2026-08-24 before release. They are retained
only as research evidence and are not production modes, dependencies, or UI.

The initial supported receiver is a stock Fire TV Stick in its **Enable
Display Mirroring** screen. The receiver must not need a sideloaded app or a
configuration change.

A representative success case is watching browser video on the laptop and
displaying it smoothly, with synchronized audio, on the television. This is
screen mirroring, not a service-specific cast protocol: the plugin sends the
pixels and audio produced by the desktop. It will not automate login, bypass
DRM, or promise that a browser permits protected video capture. Browser video
must be tested explicitly before release because content protection, motion
smoothness, continuous internet access, and audio/video sync all matter.

### Target experience

1. Install the repository with `omarchy plugin add <git-url> --enable`.
2. Put the Fire TV in Display Mirroring. Press Super+Alt+C or click the bar icon
   to open the current-display workflow without replacing Universal Copy.
3. Use ↑/↓ to highlight a discovered receiver and press Enter once to start;
   clicking a receiver starts the same action immediately.
4. The package-owned exact-purpose Polkit action prepares networking without a
   recurring password prompt for the active local user.
5. See progress through discovery, P2P, RTSP, capture, and streaming.
6. Press Stop and return to the exact prior desktop/network state.
7. If anything fails, see a short actionable reason and a contextual recovery
   action. Detailed bounded history remains available through the controller
   CLI rather than expanding the everyday panel.

### Local authorization boundary

The passwordless Polkit action deliberately trusts the active local desktop
session to request the single fixed `prepare` operation. Code already running
as that user can keep a valid cast lease renewed and temporarily disrupt the
same user's normal networking. It cannot select a privileged executable or
escape the helper's closed argument and ownership checks. Requiring Polkit
authentication would restore a per-cast password prompt without protecting the
active session from software it already runs, so this same-session
denial-of-service exposure is an accepted product tradeoff. Inactive and remote
users remain denied, and lost renewal triggers bounded independent recovery.

### Non-goals for the first release

- A native streaming-service integration, URL handoff, or Chromecast-style
  media cast.
- Automatic navigation of the Fire TV into Display Mirroring.
- Sideloading or modifying the receiver.
- Remote-control input from the Fire TV.
- 4K, HDR, HEVC, or multi-receiver streaming.
- Alternate quality profiles or a compositor-created extended display.
- Support for every compositor, distro, firewall, or Wi-Fi chipset. The first
  release targets Omarchy 4, Hyprland, Arch packages, and the hardware that is
  actually validated.

## 2. Current truth

### Proven on this machine

- FluxCast can discover the household Fire TV as a WFD peer. Its advertised
  personal label and radio address were redacted before public distribution.
- Making the Fire TV the P2P group owner with the direct wpa_supplicant backend
  works. The laptop joins as a P2P client and receives DHCP from the Fire TV.
- After opening TCP 7236 on only the P2P interface, the Fire TV completes WFD
  RTSP negotiation and starts RTP playback.
- A 1280x720p30 H.264/AAC test pattern reached the stock Fire TV with visible
  video and audible sound.
- One concurrent internet/Miracast test-pattern run lasted more than six
  minutes and sent more than 212 MB without interface drops or TX failures.
- Hyprland output discovery via `hyprctl monitors -j` works in the patched
  FluxCast checkout.
- Real `eDP-1` capture through wf-recorder, Intel VAAPI H.264, and AAC reached
  the Fire TV and was visually confirmed.
- The guarded scripts restore NetworkManager, the temporary DHCP client,
  session-scoped supplicant broker, and firewall rule after success,
  cancellation, and several failure paths.
- The package-owned controller/helper path has now completed repeated live
  mirror runs and clean teardown. After instrumenting each media stage and
  testing multiple pacing strategies, the user accepted the current 720p30,
  7 Mbps GPU Screen Recorder profile as the release candidate.
- One later package-owned real-desktop session ran for 20.5 minutes and cleaned
  up normally with no radio retry, failure, beacon-loss, or reported mux-drop
  counters. The user still saw a roughly 100 ms, almost periodic stutter, so
  the media path remains a release candidate rather than a smoothness pass.
- Revision 34 repeated that result on the consolidated screen-only product:
  discovery, negotiation, internet coexistence, and cleanup passed, while the
  user still saw video stutter and heard audio glitches. A controlled FLV
  handoff A/B was materially worse and unwatchable on the Fire TV despite its
  better offline loopback cadence. FLV is therefore rejected as a default and
  smooth-motion acceptance remains open on the Matroska path.
- Offline sustained-motion isolation for revision 35 found that Matroska A/V
  interleaving is frame-size sensitive even while GSR capture holds 30–31 fps.
  A variable-rate MPEG-TS handoff reduced ≥50 ms timestamp stalls from 202 to
  seven and eliminated ≥75/100 ms stalls over a 44.5-second repeat, without
  moving PCR, CBR, PID, pacing, or audio correction out of the proven final
  mux. Receiver A/B subsequently rejected MPEG-TS as still visibly stuttery.
  The research patch remains tracked but is excluded from the production series.
- A single-variable 1280x720p60 Matroska run then negotiated 60 Hz, held about
  60 fps with no reported dropped or duplicated frames or radio retries, and
  was described by the user as perfect. This is now the Safe release profile;
  repeatability and longer independent acceptance remain open.
- The fresh installed revision-36 default path repeated 1280x720p60 without a
  diagnostic override, held 59.22–60.48 measured fps after startup with zero
  drops or duplicates, and passed the user's visual and audio check. Normal
  Stop restored Wi-Fi and removed every resource owned by that session.
- Post-acceptance UI review found that generic WFD advertisements still looked
  actionable and that a button launch could fail before producing controller
  state. Release discovery now exposes only the validated Fire TV class. The
  panel snapshots the selected receiver for its child process and reports a
  bounded launcher error instead of silently returning to idle. Nerd Mode uses
  a two-column card grid and omits unavailable packet/A/V cards.
- The native Omarchy panel exposes live negotiated mode, capture/mux load,
  packet timing, RTP queueing, radio counters, and a derived health verdict.
- The panel launches production work in `omacast-session.service`; a simulated
  streaming session remained owned and active across an actual Omarchy shell
  restart. Lost lock ownership is derived as a recoverable error rather than a
  permanently active cast.
- The detached service owns a logind sleep/idle inhibitor and the plugin owns a
  compositor idle inhibitor for the same active lifetime. Normal Stop and
  forced owner death both release the inhibitor automatically.
- Live status includes the session age alongside measured pipeline health, and
  private session history is bounded to the newest 50 sessions.
- Normal teardown and explicit user-owned recovery remove known volatile
  telemetry from `$XDG_RUNTIME_DIR`; archived samples are pruned alongside the
  same 50-session event-history boundary. Independent privileged recovery
  restores networking but never traverses or deletes user-owned telemetry.
- The installable plugin is branded **Omacast** while retaining the stable
  `hardie.omarchy-cast` plugin ID for in-place upgrades. Super+Alt+C is the
  intended optional summon gesture and leaves Omarchy's stock Super+C Universal
  Copy binding intact. The earlier Super+C choice is superseded.
- Marketplace review at public commit `965f94d` found that the privileged
  command surface was carefully hardened but identified missing runtime data
  ceilings between discovery, the controller, runtime JSON, and QML. The
  incremental remediation retains the architecture and media path: discovery
  subprocesses keep at most 65,536 bytes per stream, controller UI documents
  are capped at 262,144 bytes, and QML replaces its complete-output collector
  with a streaming 262,144-character ceiling. State is capped at 65,536 bytes
  before parsing, telemetry is descriptor-read under its existing
  262,144-byte ceiling, and QML allowlists bounded receiver/readiness/warning/
  session models. Every controller- or radio-derived label is rendered as
  plain text. Adversarial
  fixtures cover excess output, deep/oversized JSON, links, receiver floods,
  control characters, and markup-like labels without running P2P or changing
  networking.
- Follow-up marketplace review at public commit `540f578` found that the
  descriptor reader could still wait while opening a FIFO before its
  regular-file checks ran. Runtime state and current telemetry now open with
  `O_NONBLOCK` as well as no-follow semantics, so descriptor validation rejects
  a pipe without joining it. A subprocess regression test has its own deadline
  and proves that the public status path returns rather than hanging.
- The same follow-up found that the root guard accepted a user-owned media PID
  and applied negative nice after checking user-spoofable process attributes.
  Guard API 5 removed the PID file, process traversal, and privileged `renice`
  surface. The existing user transient service applies `CPUWeight=10000` to its
  own supervised cast cgroup instead. Receiver acceptance on 2026-08-25 confirmed the
  property on the live service, no PID channel, stable 720p60 playback, and
  complete owned cleanup without a privileged scheduling action.
  Revision 41 / guard API 7 additionally pins the verified user heartbeat inode and performs
  bounded reads through that descriptor, closing the adjacent special-file
  replacement race in both the guard and independent recovery process. Its
  user-writable markers live below a root-owned session parent, eliminating the
  directory-symlink race, and the unused privileged Stop verb is removed. Its
  root-owned session record scopes teardown to P2P clients observed after the
  guard established a clean baseline. Enumeration happens once at teardown,
  before NetworkManager resumes, rather than polling during media delivery.
  The controller accepts cleanup only after the exited helper returns the
  matching session's bounded, schema-valid `cleaned` status.
  Receiver acceptance on 2026-08-25 then negotiated 1280x720p60, stabilized at
  59.5 measured fps with no dropped or duplicated frames, and passed the
  user's picture and audio check. Normal UI Stop removed the session-created
  P2P-client interface, transient service, helper/media processes, session
  runtime children, and temporary network/DBus files while restoring the
  connected infrastructure Wi-Fi and original network-service state.
  A subsequent marketplace follow-up found that the unprivileged
  `session.lock` still used a path-following open and pathname `chmod`. The
  lock and its read-only ownership probe now open through validated runtime
  directory descriptors with no-follow/nonblocking flags, validate the lock
  inode's type, owner, and link count with `fstat`, and apply its mode only with
  `fchmod` on that descriptor.
- Runtime state, live telemetry, and archived telemetry now retain validated
  private directory descriptors for their complete operation. Atomic state and
  snapshot replacement, cleanup, retention, and archive append are relative to
  those descriptors. FluxCast receives paths to preopened telemetry inodes
  through the controller's `/proc/<pid>/fd` entries, so a later pathname swap
  cannot redirect engine output to another user file.
- The renewable user heartbeat is created and validated without truncation,
  then retained as one nonblocking descriptor through the complete session.
  Renewals update only that inode, matching the privileged guard and recovery
  processes that independently pin the same heartbeat before reading it.
- NetworkManager pause/resume signals are resolved through the systemd unit at
  signal time rather than a numeric PID retained across authorization and cast
  lifetime. A private root-owned marker records that this session may require a
  resume; cleanup and independent recovery validate it, retry unit-scoped
  `SIGCONT`, and never signal a recycled unrelated process ID. A revision-46
  GUI run subsequently negotiated 1280x720p60, streamed at approximately
  60 fps, and completed cooperative Stop with NetworkManager active, the P2P
  client removed, and infrastructure Wi-Fi connected.
- Receiver-facing RTSP input now has explicit line, header-count, aggregate
  header, and body ceilings. Negotiation and partial messages have a ten-second
  completion deadline, established sessions may remain legitimately idle, and
  the passive listener admits at most four concurrent workers. Oversized,
  malformed, truncated, stalled, and excess-connection cases fail before they
  can begin a new capture pipeline. A revision-44 GUI run then connected to the
  stock Fire TV, streamed with picture and sound accepted by the user, and
  returned to idle through GUI Stop with the P2P client removed and normal
  infrastructure Wi-Fi connected.
- Unlimited casts now keep diagnostics bounded independently of cast lifetime.
  The persistent per-session telemetry archive stops at 8 MiB while the live
  snapshot continues, FluxCast output is continuously drained into a 256 KiB
  recent tail, and FFmpeg progress on the supported desktop path retains only
  its latest complete record. The production-only packet-trace override was
  removed; no diagnostic quota stops or shortens the media session. A
  revision-45 GUI run then negotiated 1280x720p60, exposed current progress in
  Nerd Mode, stabilized around 60 fps without FFmpeg drops or duplicates, and
  completed cooperative Stop with the P2P client removed and infrastructure
  Wi-Fi connected.
- A correctly targeted Super+C run has now completed Fire TV P2P, DHCP, RTSP,
  and 1280x720p30 negotiation. It exposed a media-boundary incompatibility:
  GPU Screen Recorder rejects the shared-memory frames produced when the
  Hyprland portal's window DMA-BUF negotiation falls back. Cleanup restored the
  exact recorded host network and inhibitor baseline.

### Not yet proven

- Repeatable soak results. One 20.5-minute real-desktop session completed, but
  the user observed periodic stutter and the required repeated matrix is open.
- Repeatable 30-minute acceptance runs on another machine and receiver.
- End-to-end protected browser-video playback. DRM capture behavior, motion,
  audio sync, and long-running internet coexistence are unknown.
- General receiver, Wi-Fi adapter, interface-name, monitor-name, firewall, or
  GPU support.
- A hosted companion package. The Arch recipe is reproducible, but Omarchy's
  plugin manager cannot install it and marketplace users must install it
  explicitly until a trusted package channel exists.

### Broad-support blocker

Radio coexistence is the first product blocker. This laptop has one Wi-Fi
adapter. Its normal station connection and the Fire TV P2P client can sometimes
coexist on two 2.4 GHz channels, but the result is not repeatable:

- infrastructure Wi-Fi on 5 GHz channel 149 plus P2P on 2.4 GHz channel 1
  disconnected quickly;
- infrastructure Wi-Fi around channel 6 plus P2P on channel 1 once ran for
  more than six minutes, but a later equivalent real-desktop run failed after
  about 12 seconds;
- the Fire TV rejected the requested 5 GHz operating channel.

Online video makes concurrent internet mandatory. The accepted single-machine
path is good enough for this hardware-specific release candidate, but it is not
yet evidence for broad adapter support. The preferred robust configuration
remains a dedicated Wi-Fi adapter for P2P. A single adapter may be advertised
beyond the validated host only after repeated same-channel or
compatible-channel tests pass.

### Important repository facts

- A publication privacy audit removes private receiver identifiers, observed
  household network addresses, and personal contact details from the public
  snapshot. Public releases must be created only from a parentless sanitized
  history using a non-personal noreply commit identity; the release tag and
  generated source archive are part of the same audit boundary.
- The first complete release-candidate tree was committed on `master` as
  `1026b5b`; subsequent readiness, launch-cancellation, and final panel-state
  hardening is tracked through implementation commit `a174ba5`. The repository still has no
  configured public remote.
- `work/` remains ignored research state and is not a production dependency.
  A fresh local clone of `1026b5b` passed the official Omarchy validator and
  the complete controller suite without it.
- The FluxCast compatibility history is preserved as 34 numbered patches under
  `patches/` and applied by the tracked Arch recipe to pinned upstream commit
  `9d27c39`. The production series applies 28 receiver-relevant patches:
  1–6, 9–22, and 27–34. Portal-only patches 7–8, rejected FLV patch 23,
  portal/window patches 24–25, and rejected MPEG-TS patch 26 remain tracked
  research and are excluded from shipping.
- The live host now runs `fluxcast-omarchy-cast 0.1.5.r3.omarchy-36` with all
  191 package files intact. Its engine exposes the five required production
  flags and exposes neither the private handoff nor portal-source flags. The
  matching plugin clone is clean, validates, and is the sole configured widget.
- Historical revision 24 contained the then-current patched engine and passed
  `pacman -Qkk` with zero altered files.
- Package revision 25 promotes every supported Miracast runtime command to a
  declared dependency. A clean build from
  implementation commit `a174ba5` passed 96 engine tests and emitted a
  checksum-valid artifact with SHA-256
  `2e802cc67164b5d2bc7af72f825cab1a4591f0d59ad46b0a6cedb9cd7ad4c659`.
- The current development tree advances the recipe through revision 28 for an
  until-stopped renewable lease, post-boot runtime creation, and exact
  systemd-networkd state restoration, plus a machine-readable guard API
  revision. The actual revision-24 installation is now correctly rejected as
  incompatible by the development readiness probe. The exact-clean builder
  produced revision 28 from commit `3dc69a0`; a disposable pacman root proved
  in-place revision-27 to revision-28 upgrade, helper API revision 2, package
  integrity, and complete removal. Revision 28 changes no runtime behavior; it
  redacts private receiver identity from retained sources and parameterizes the
  lab launchers. It has not yet been installed on the live host or exercised
  against the receiver.
- Revision 29 additionally makes the independent missed-heartbeat recovery
  path remove the same fixed set of volatile live telemetry filenames as the
  unprivileged controller, including `qos.pid`, before removing the empty
  session directory. This closes an orphaned-controller cleanup gap without
  allowing caller-provided paths or recursive deletion. Exact-clean build and
  live forced-owner acceptance remain pending. The exact-clean builder then
  produced revision 29 from commit `f3c2faf`; all 23 engine patches and 97
  engine tests passed, as did the artifact audit, candidate-only lifecycle,
  and disposable revision-28 to revision-29 upgrade/removal lifecycle.
- Revision 30 removes the abandoned display/profile controls and obsolete
  persisted-defaults surface, leaving one mirror/Safe production contract. It
  also replaces ambiguous privileged-helper dispatch with explicit control
  flow and adds production ShellCheck to release CI. The exact-clean build from
  commit `ee522b8` applied all 23 patches and passed 97 engine tests; artifact
  audit, candidate-only lifecycle, and revision-29 to revision-30
  upgrade/removal passed. Its SHA-256 is
  `40d77709b9fc2c17fd1053f69c0656f5d406c2a365dbcbf0e37ca02dd9085a3f`.
  Live installation and receiver-backed acceptance remain pending.
- Revision 31 adds keyboard-first contextual window casting without weakening
  explicit receiver choice: discovery highlights but does not select, and one
  Enter selects the highlighted destination and starts. Exact commit `66ab7e4`
  applied all 24 patches and passed 98 engine plus 109 controller/plugin tests.
  The artifact audit, candidate-only lifecycle, and revision-30 to revision-31
  upgrade/removal lifecycle passed. Its SHA-256 is
  `637f96439e5da63a4ef86300a245cd17f30da2054ec9cd7c3c23e7bfc094af6d`.
  An isolated fake-controller shell twin loaded without QML errors and issued
  exactly one window-source start request on Enter; no live network path ran.
- Live revision-31 acceptance then exposed a repeat-cast ownership defect: a
  socket-activated `systemd-networkd` service was a valid restored host state,
  but the next guard rejected any already-active service. Revision 32 records
  and supports that state, reloads its session-scoped network file when the
  service is already running, and restores/reloads the exact recorded unit
  states on both normal and independent cleanup. Unprivileged teardown now
  signals the owned stop marker first and preserves the original helper error
  if the elevated PID cannot be signalled directly.
- Revision 33 replaces the failed GSR window route with FluxCast's SHM-capable
  GStreamer PipeWire reader feeding FFmpeg VAAPI and the same Safe receiver-
  facing pacer. It removes the obsolete GSR-source engine flag, requires a
  typed single-window portal result, declares the new runtime dependencies,
  and withholds streaming state until FFmpeg proves an encoded video frame.
  Its exact-clean build, 101 engine tests, artifact audit, fresh package
  lifecycle, and revision-32 to revision-33 lifecycle pass. The audited package
  is installed on the live host with 191/191 files intact, the engine capability
  and all dependencies pass doctor, and the plugin upgraded in place. Receiver
  acceptance remains pending.
- The patched-supplicant timeout experiment explained an earlier failed role
  configuration. It is not part of the successful Fire-TV-as-group-owner path
  and must not become a release dependency without new evidence.
- Several research scripts hard-code `wlp58s0`, `eDP-1`, the Fire TV MAC, P2P
  interface globs, resolution, and timing. They are preserved under
  `scripts/lab/` as evidence and diagnostic tools, not production controller
  code.
- Omarchy 4.0 uses a root `manifest.json` with `schemaVersion: 1`. The plugin
  should be validated with `omarchy plugin validate <repo>`. Omarchy's plugin
  installer only clones, validates, and enables a repository; it does not run
  install hooks, install packages, or use sudo.

### Host snapshot on 2026-08-21

- Omarchy `4.0.0-1`
- NetworkManager `1.58.0-1`
- wpa_supplicant `2.12-1`
- FluxCast AUR package `0.2.2.r0.g9d27c39-1`
- wf-recorder `0.6.0-2`
- FFmpeg `9.0.1`
- Intel VAAPI and libx264 H.264 encoders available
- PipeWire audio monitor detected
- primary Wi-Fi `wlp58s0`, connected on 5 GHz channel 149 at the time of the
  latest doctor run; this is a known-bad topology for the Fire TV test
- no UFW or firewalld front-end detected by the latest doctor run; this does
  not by itself prove that nftables permits the P2P RTSP port

The harmless doctor warnings about xrandr, GStreamer x264, and `dbus_next` do
not block the chosen Hyprland + wf-recorder + FFmpeg path.

## 3. Product architecture

Keep presentation, orchestration, privileged networking, and the media engine
separate. This makes failure recovery testable and keeps unsafe logic out of
the long-running shell process.

```text
Omarchy bar widget/panel (QML)
  -> omarchy-cast CLI (small, unprivileged control/status API)
    -> per-session supervisor (user process/transient user service)
      -> narrowly scoped privileged network helper, when required
      -> compatible FluxCast build
        -> wpa_supplicant P2P + DHCP + RTSP
        -> wf-recorder -> FFmpeg VAAPI/libx264 -> RTP
    -> atomic state + structured logs under XDG runtime/state directories
```

### 3.1 Omarchy UI

Use a root `manifest.json` with a non-reserved id such as
`hardie.omarchy-cast`, kind `bar-widget`, and `entryPoints.barWidget` pointing
to a panel-style QML entry point. Follow the installed Network and Tailscale
panels for layout, process execution, keyboard navigation, and theme use.

The QML layer should:

- show idle, unavailable, scanning, connecting, streaming, stopping, and error
  states;
- show receiver, mode, quality, elapsed time, and a concise network warning;
- invoke the controller with argument arrays, never assembled shell strings;
- poll or watch machine-readable status produced by the controller;
- remain responsive while scans and streams run;
- provide only Scan, Connect/Cancel, Stop, and contextual Recover actions;
- integrate live health signals in the cast view while keeping detailed
  bounded history in the CLI;
- resolve bundled executables relative to the plugin directory rather than
  assuming a working directory;
- never manipulate NetworkManager, wpa_supplicant, firewall rules, Hyprland
  outputs, or encoder processes directly.

Keep the first UI narrow. Device renaming, favorites, settings schemas, and
automatic reconnect can follow after the session lifecycle is reliable.

### 3.2 Controller and state machine

Create one executable control surface, tentatively `bin/omarchy-cast`, with
both human output and stable JSON output:

```text
omarchy-cast doctor [--json]
omarchy-cast scan [--json]
omarchy-cast monitors [--json]
omarchy-cast connect --peer <id> --mode mirror --profile safe
omarchy-cast status --json
omarchy-cast stop
omarchy-cast recover
omarchy-cast logs [--last]
```

Every controller surface—including read-only planning, local probes,
simulation, state validation, and service-owned execution—accepts only the
mirror/Safe mode and profile. The controller always selects the proven display
capture source; no public source selector or portal dependency remains. Historical
experiments remain in the research log; abandoned choices do not remain latent
in production code.

Implement the controller in Python 3 with the standard library wherever
practical. Treat FluxCast as a versioned subprocess/API boundary rather than
importing unstable internals into the UI. Version the JSON status schema and
use stable error codes so the panel does not need to parse human log text.

The controller must dynamically discover the Wi-Fi interface, P2P device,
Hyprland outputs, default audio monitor, render node/encoder, firewall state,
and receiver identity. No user-specific MAC, interface, monitor, subnet, or
home path may appear in production defaults.

The media engine must authenticate every inbound or source-initiated RTSP peer
against the selected receiver and the session-owned P2P interface before RTSP
negotiation or capture begins. Only one authenticated receiver may own a media
session. Ambiguous neighbour identity fails closed; a conventional LAN address
must never be used as an unverified fallback.

Use an explicit state machine:

```text
idle -> checking -> discovering -> preparing -> connecting -> streaming
  ^                                                        |
  +---------------- stopping <- error/recovering <---------+
```

Each transition writes an atomic JSON state file below
`$XDG_RUNTIME_DIR/omarchy-cast/`. Only one session may own the lock. Store
durable, structured session logs below
`$XDG_STATE_HOME/omarchy-cast/sessions/` with bounded retention. Logs should
include transition timestamps, selected topology, negotiated WFD mode,
process exits, cleanup results, and useful radio counters, while excluding
credentials and unrelated network data.

Every active-state and durable-history session identifier is exactly 32
lowercase hexadecimal characters. Event logs and Stop requests are opened or
replaced relative to validated private directory descriptors. Reads are
nonblocking, bounded, no-follow, current-user-owned, private, regular, and
single-link; malformed or unsafe entries never become history or control data.
State and telemetry directory identities remain pinned across atomic writes,
engine handoff, sampling, cleanup, and archive retention. Engine output files
are created and validated before FluxCast starts; subprocesses address those
exact inodes rather than reopening user-replaceable telemetry pathnames.

Run the streaming supervisor outside `omarchy-shell`, preferably as a named
transient `systemd --user` service. A shell reload must not orphan the stream;
logout must stop it. The supervisor owns all child processes and performs
ordered cleanup even when one cleanup step fails.

### 3.3 Privileged network boundary

The initial research solution temporarily paused NetworkManager, started a
narrowly matched systemd-networkd DHCP client, opened one P2P-only TCP port,
and extended wpa_supplicant D-Bus access. The production replacement no longer
grants the desktop user's UID supplicant mutation rights: a root-owned session
broker accepts only fixed connect and cleanup requests pinned to the selected
adapter, receiver, and frequency.

Build a single audited helper with a very small command/argument surface. It
must:

- validate interface names, UIDs, time limits, paths, and session ownership;
- make only session-scoped changes;
- refuse to overwrite pre-existing configuration or operate when ownership is
  ambiguous;
- use a unique token and an independent maximum-duration recovery path;
- establish renewable lease files without following, blocking on, or
  truncating an unvalidated inode, and retain the verified inode across
  renewal;
- restore the exact prior NetworkManager/systemd-networkd/firewall state;
- expose deterministic status and cleanup results to the supervisor;
- support cancellation during discovery, authorization, connection, and media;
- never accept an arbitrary command, arbitrary file source, shell fragment, or
  unrestricted path from QML;
- never traverse or remove user-owned telemetry; normal and explicit stale
  cleanup remain responsibilities of the unprivileged controller;
- never install a temporary per-user system-bus policy for supplicant mutation.

The session broker must retain its closed request schema, exact receiver and
adapter binding, one-connect limit, owned-WFD-value check, bounded lifetime,
and independent cleanup tests.

### 3.4 FluxCast delivery

Do not make the production plugin depend on the ignored `work/fluxcast`
checkout or apply patches at runtime.

Recommended path:

1. Rebase the three commits onto current upstream FluxCast and make the test
   suite clean in a reproducible environment.
2. Submit generally useful changes upstream in logical units.
3. Until they are released upstream, maintain a clearly named fork and an Arch
   package pinned to a known compatible commit.
4. Add a machine-readable capability/version probe so the plugin can say
   “compatible engine installed” rather than relying on a package name.
5. Keep the plugin and engine integration versioned. Fail with setup guidance
   when required capabilities are missing.

Implementation note (updated 2026-08-24): readiness probes the production
engine capabilities supplied by the production patch series plus the versioned unprivileged
guard API. Rejected experimental handoff flags are deliberately not required.
An older or incomplete companion is routed to setup before discovery.

The Omarchy plugin manager cannot install packages. The first-use panel and
README must show explicit `omarchy pkg ...` commands, and may offer to open a
visible terminal for the user. It must not silently elevate or modify packages.

If FluxCast code is ever vendored into this repository, preserve its
GPL-3.0-or-later licensing and source notices and make that a conscious release
decision. A maintained fork/package is preferred because it keeps engine tests
and upstream synchronization independent from the QML plugin.

### 3.5 Capture and quality contract

Ship only the receiver-validated Safe profile:

| Profile | Initial target | Purpose |
| --- | --- | --- |
| Safe | 1280x720p60, VAAPI preferred | Establish compatibility and recovery |

The controller must distinguish the requested, advertised, negotiated, and
actual encoder modes. It should fall back only with a visible explanation.
Never claim a mode that the receiver did not negotiate.

Tune and measure:

- frame pacing and dropped/duplicated frames at capture and encode;
- encoder utilization, bitrate floor, VBV size, GOP, and receiver buffering;
- RTP continuity, Wi-Fi retries/failures, P2P beacon loss, and disconnects;
- audio clock correction, drift, and subjective lip sync;
- end-to-end motion quality using real moving content, not only a synthetic pattern.

VAAPI should be preferred when it passes a smoke test, with libx264 as a
clearly reported fallback. GPU selection must be capability-based rather than
“first `/dev/dri/renderD*`”.

While streaming, hold an appropriate user-session idle/sleep inhibitor and
release it during every stop/recovery path. Warn that mirroring can expose
notifications and other desktop content.

### 3.6 Mirror ownership

Mirror mode captures a selected existing Hyprland output. Default to the
currently focused output, but let the user choose.

Do not write persistent Hyprland configuration for a normal cast session.
Exercise cleanup after compositor reload, shell reload, and logout.

### 3.7 Radio policy

Topology selection belongs in readiness and connect preparation:

1. Inventory physical radios and valid managed/P2P concurrency combinations.
2. Prefer a dedicated P2P-capable adapter when present.
3. With one adapter, detect the infrastructure band/channel and warn before a
   known-bad combination.
4. Offer a compatible saved 2.4 GHz connection only with explicit user
   confirmation; record and restore the prior connection.
5. Never sacrifice internet silently. Online browser video is unusable if
   switching topology removes internet access.
6. If the adapter cannot meet the validated support matrix, fail early with a
   recommendation for a tested second adapter instead of starting an unstable
   session.

Automatic network switching is a later feature, after manual topology changes
and restoration are reliable. The plugin must explain when a router channel
change is outside its control.

## 4. Delivery phases and gates

Do the phases in this order. A phase passes only when its exit gate is
recorded with logs and the repository documentation is updated.

### Phase A — make the research reproducible

Implementation note (updated 2026-08-22): the tracked controller now supplies
read-only doctor/monitor/status probes, live named-receiver discovery,
versioned state and telemetry, guarded production connect, cooperative Stop,
stale-state recovery, bounded private logs, and safe offline lifecycle and
protocol fixtures. `scripts/bootstrap-fluxcast` and the Arch recipe recreate
the pinned engine from upstream commit `9d27c39` plus the complete 28-patch
series. A fresh clone of release-candidate commit `1026b5b` passes the official
Omarchy validator and all non-hardware controller tests without `work/`.

- Configure a top-level public remote and trusted package/release channel.
- Rebuild the companion from a clean public clone as part of release CI. The
  tracked exact-commit builder and pinned-action workflow now implement this
  path locally; the hosted run remains pending publication of the repository.
- Add `shellcheck` to release CI when the public repository is created; local
  `bash -n` and guard ownership/cleanup tests already pass.

Exit gate: a fresh clone can build/install the exact engine and pass all
non-hardware tests without files from the original workstation.

### Phase B — settle radio reliability before building UI

Progress note (2026-08-21): a guarded 720p30 test pattern and a guarded
720p30 real `eDP-1` desktop capture were both visibly successful on the Fire
TV. The desktop capture ran for five minutes without a process or transport
drop; the user found it readable and good, with expected Miracast latency.
Both sessions restored the temporary network state and the saved Wi-Fi profile
afterward. This is a useful feasibility confirmation, not the Phase B exit
gate: the one-radio topology still needs the repeatable ten-minute matrix with
concurrent internet traffic before it can be automated or advertised.

Run a logged topology matrix. Each candidate configuration must complete at
least three consecutive 10-minute test-pattern runs and three consecutive
10-minute real-desktop runs while normal internet traffic remains active.

Test in this priority order:

1. dedicated P2P Wi-Fi adapter plus normal internet on the built-in adapter;
2. one adapter with infrastructure AP and Fire TV P2P on the same 2.4 GHz
   channel, preferably channel 1;
3. one adapter on nearby 2.4 GHz channels only if repeated tests prove it.

Record adapter/driver, station and P2P channels, signal, bitrate, retries,
beacon loss, negotiated media mode, transmitted bytes, and disconnect reason.

Exit gate: one topology is designated supported and repeatably passes. If no
single-radio topology passes, the initial product requirement becomes a tested
second adapter; do not continue pretending one-radio support is reliable.

### Phase C — prove Safe with real browser video

- Validate the Safe profile.
- Run 30-minute real-desktop sessions with ongoing internet use.
- Test ordinary browser video, then a live/replay streaming service with the
  user's normal account and browser.
- Check for black/protected video, resolution/fps negotiation, visible stutter,
  audio presence, sync at start and near the end, fullscreen behavior, and
  notification/idle interactions.
- Do not bypass content protection. If the browser returns black frames,
  document protected-service capture as unsupported while keeping generic
  desktop casting valid.

Exit gate for the mirror-first 0.1 release: Safe completes three 30-minute
sessions with usable audio/video, continuous internet, and clean teardown.

### Phase D — extract the production controller and helper

- Replace every hard-coded machine value with discovery or validated config.
- Implement the CLI/state machine, single-session lock, atomic state, bounded
  logs, timeout policy, and ordered cleanup.
- Refactor the guarded networking experiment into the audited helper boundary.
- Make stop and recover idempotent.
- Add failure-injection tests at every transition.
- Preserve research scripts under an explicit `research/` or `scripts/lab/`
  namespace so they cannot be mistaken for the production path.

Exit gate: mirror sessions can be driven entirely through the CLI, and normal
network/desktop state is restored after success, Ctrl+C, process kill, shell
reload, failed DHCP, failed RTSP, failed capture, and authorization cancel.
The CLI/helper implementation is now present; gathering that live
failure-injection and repeatability evidence still requires a receiver in
Display Mirroring mode.

Offline media-failure note (2026-08-26): production patch 30 makes the
two-child GSR/FFmpeg launch transactional. A failure or interruption before
readiness closes the capture pipe and reaps every started child with bounded
terminate/kill escalation; only a healthy pair becomes normal pipeline state.
The five startup failure/success regressions pass in the exact reconstruction.

Offline RTSP ownership note (2026-08-26): production patch 31 distinguishes a
selected receiver's unconfirmed TCP reservation from validated RTSP progress.
The active fallback may supersede only an unconfirmed generation; stale
handlers cannot dispatch or release the replacement, while a confirmed passive
session still cancels fallback. Offline race and identity regressions pass. A
short receiver-backed negotiation and Stop run was retained because this
changes the successful RTSP ownership path.

Receiver acceptance (2026-08-26): revision 50 completed a normal GUI
connect/stream/Stop run against the stock Fire TV. The controller returned to
idle with complete helper cleanup, no media or session P2P processes, active
NetworkManager, and connected infrastructure Wi-Fi. This closes the short
patch-31 receiver gate; longer reliability gates remain unchanged.

Offline supplicant-ownership note (2026-08-26): production patch 32 resolves
the selected adapter's exact supplicant control path before session startup,
records the selected peer's existing group set before Connect, and accepts only
one newly attributable group on that adapter. Teardown sends Cancel and
Disconnect only to the recorded control path. Multi-adapter, pre-existing-group,
ambiguous-group, and failed-connect regressions pass. Because successful group
selection changed, revision 51 retains a short receiver-backed connect/stream/
Stop acceptance gate before release.

Offline authorization note (2026-08-26): production patch 33 and guard API 10
replace the temporary UID-wide wpa_supplicant D-Bus policy with a root-owned
session broker. The user socket accepts only versioned `connect` and `cleanup`
operations; the authenticated guard pins adapter, receiver, frequency, and
session before the broker starts. Connect is single-use and waits for the
root-owned network-ready marker. P2P cleanup begins only after this session
attempts Connect, and WFD metadata is cleared only while its exact value and
root-owned marker still prove ownership. The combined revision-51/52 receiver
gate passed: the retry connected and streamed cleanly, and GUI Stop restored an
idle controller, connected infrastructure Wi-Fi, empty WFD metadata, and no
session P2P interface, broker state, or media process.

Offline shutdown note (2026-08-26): production patch 34 makes SIGTERM enter the
same WFD unwind as Ctrl+C, then restores the caller's prior signal handler. The
session now owns and cancels the active-probe thread and outbound socket, joins
it with a deadline, and registers probe-started media before spawning children
so ordinary media cleanup can always reach it. Stop during initial probe wait,
peer lookup, connection, media startup, and the live session has direct
regression coverage. Package revision 53 retains guard API 10. Because this
changes the successful GUI Stop path, it was installed and exercised through
the real panel against the stock Fire TV. The session negotiated 1280x720p60,
streamed, and then accepted UI Stop through the cooperative cancellation path.
The controller returned to idle, infrastructure Wi-Fi remained connected, the
session-created P2P interface disappeared, the user service became inactive,
and no engine, guard, broker, capture, or mux process remained. This closes the
revision-53 receiver-backed start/Stop gate.

Offline package-scope note (2026-08-26): production patch 35 narrows the
companion's public engine to the WFD product boundary. The parser accepts only
`--protocol wfd`, no tray or LAN/Cast option is advertised, and the wheel
excludes the tray, Chromecast, DLNA, and HTTP-server modules. Package revision
54 also drops their Pillow, pystray, pychromecast, and upnpclient dependencies.
CLI rejection tests, the exact 29-patch engine suite, the repository suite, the
artifact audit, and a disposable package lifecycle pass. The accepted WFD
capture, media, network, and privileged-helper paths are unchanged.

Offline input-surface note (2026-08-26): production patch 36 removes the
experimental WFD UIBC back channel, which was not part of Omacast's product
scope and accepted unauthenticated TCP input on every host interface. Package
revision 55 has no UIBC CLI flag, RTSP negotiation, firewall lifecycle,
listener, `/dev/uinput` injector, module, or packaged symbol. Negative CLI and
artifact checks prevent the surface from returning. Together, patches 35 and
36 remove both conditional network-service branches identified by the audit;
ordinary authenticated RTSP and RTP streaming are unchanged.

### Phase E — build the Omarchy plugin UI

Implementation note (updated 2026-08-24): the repository contains a schema-version-1
root `manifest.json`, a thin `ui/Panel.qml` bar widget, and a package-owned
session helper. The controller issues a fixed, versioned `pkexec` request for a
validated UID/interface/session/duration; the helper generates only
session-named network state and a closed-operation supplicant broker, and an
independent recovery process bounds cleanup. Guard API revision 10 binds the
requested UID to
Polkit's authenticated caller, while the installed declarative action permits
only the exact guard executable with `prepare` as its first argument for an
active local user. The compact panel discovers Miracast receivers by name,
offers one Safe stream contract and exposes only contextual Cancel, Scan, Stop,
and Recover actions. It never selects the first receiver implicitly: a mouse
click casts the chosen receiver immediately, while ↑/↓ and Enter provide a
complete Omarchy-style keyboard path. N toggles Nerd Mode and Q stops the
live cast. Super+Alt+C uses
the shell's native toggle route and the bar icon opens the same current-display
workflow without reading window metadata, taking a screenshot, or opening a
portal picker. Live discovery identifies receivers without a hard-coded MAC.
`scripts/validate-plugin` stages the installable payload without the ignored
local `work/` tree. The shipped 28-patch engine series contains the proven
display route plus fail-closed selected-receiver admission and bounded RTSP
input/progress handling. Portal-only patches
7–8, receiver-rejected FLV patch 23, portal
patches 24–25, and the receiver-rejected MPEG-TS patch 26 remain outside the
series as preserved research. The
cast icon is orange during setup, green only in measured streaming state, and
red on error. Failed authorization and abruptly killed supervisor paths expose
a contextual Restore action; normal idle and streaming views remain compact.
The live section integrates session age and measured pipeline signals without a
drawer. Detailed historical events stay in the CLI instead of adding diagnostic
and file-opening buttons that do not drive the supported workflow.
The local orange start-pending state also bridges the short interval between
systemd accepting the service and the supervisor publishing state, so no
duplicate Cast or Scan action becomes available during startup.
The same action becomes **Cancel connection** throughout that interval. Stop
also cancels the transient service before state publication, accepts a stop
from the first `checking` transition, and performs a post-stop stale-state
sweep. Five consecutive installed-controller start/cancel races returned to
idle without a lingering service or inhibitor.
After streaming begins, receiver liveness also requires the session's P2P group
interface to remain present. Its disappearance for three consecutive seconds
is treated as a receiver-side stop and drives the normal owned cleanup path;
an ESTABLISHED-but-stale RTSP socket cannot keep the UI green by itself.
First-run readiness now has one controller-owned verdict covering required
commands, patched engine capabilities, both package-owned helpers, connected
Wi-Fi, a Hyprland source, and PipeWire audio. Automatic scanning cannot begin
until that verdict is ready. Missing companion components replace the receiver
controls with one native setup action that copies `makepkg -si` and opens a
visible terminal; Omacast itself still executes neither the build nor elevation.
A real-shell lifecycle test subsequently installed a temporary unique-ID twin
of the tracked payload through `omarchy plugin add --enable`, validated it,
fast-forwarded it through `omarchy plugin update`, toggled it explicitly, and
removed it. The live shell configuration returned byte-for-byte to its prior
content and the production Omacast instance was unchanged. This proves the
local Omarchy command integration without claiming the still-pending public
repository and clean-account gate.

- Add the schema-version-1 root manifest and QML bar panel.
- Match current Omarchy theme, keyboard, focus, and panel conventions.
- Connect only to the stable CLI JSON contract.
- Add first-run setup guidance, readiness details, progress, stop/recover, and
  bounded diagnostic evidence.
- Run `omarchy plugin validate .` and test add/enable/update/remove from a
  separate clone.

Exit gate: a user can install the git repository, complete setup, connect,
observe status, stop, recover, and remove the plugin without editing files or
using research commands.

### Phase F — superseded scope decision

The earlier plan to add an extended-display mode and multiple named quality
profiles is superseded. Those experiments did not pass acceptance and are no
longer product backlog items. Their evidence remains in the research log, but
their controls, defaults, validators, and test branches must not remain in the
shipping controller or UI.

### Phase G — release hardening

Local marketplace audit note (2026-08-22): the current marketplace baseline v3
scanner was rerun after final panel hardening against exact commit `a174ba5d` and
40 relevant files. It found no unsafe rules and did not block approval. Its
`review-required` outcome is expected because the documented installation and
runtime paths cross privilege, package-manager, and service-management
boundaries. Marketplace maintainers must review those capabilities; Omacast
must not describe that review-required result as an automated security pass.
The exact same upstream logic was refreshed on 2026-08-23 against current
commit `e3dce26`; it again scanned 42 relevant files, found zero unsafe
patterns, returned `review-required`, and set `blocksApproval: false` with only
the expected privilege, package-manager, and service-management capabilities.
This remains local preflight evidence, not the required public-snapshot review.
Current-contract note (2026-08-23): marketplace main `5acd4d3` still accepts
Hardware plus bar/media/quickshell and its active/retired registry does not
contain `hardie.omarchy-cast` or Omacast. The exact analyzer scanned 37
applicable files at Omacast `f272e9f`, found zero unsafe patterns, and retained
the same non-blocking review-required capability set. New listings now require
an exact fresh public snapshot and explicit `approved-and-verified` maintainer
decision. The draft tracks that contract, but publishing and issue creation
remain owner-authorized actions after receiver acceptance.

- Publish the support matrix, known limitations, dependency/fork strategy, and
  privacy/security notes.
- Add changelog/versioning and a repeatable release checklist.
- Verify installation on a clean Omarchy 4 account and at least one additional
  compatible machine/receiver before claiming general support.
- Decide which FluxCast changes have landed upstream and remove obsolete
  patches/workarounds only after the shipped dependency no longer needs them.

Exit gate: the documented fresh-install flow and all supported scenarios pass
without hidden workstation state.

## 5. Test matrix

Automate what does not require a real receiver:

- CLI argument/schema tests and JSON contract snapshots;
- dynamic interface/monitor/audio/GPU discovery with fixture outputs;
- state-machine transition and single-owner locking tests;
- subprocess exit, timeout, double-stop, and interrupted-cleanup tests;
- privileged-helper input validation and ownership-token tests;
- firewall backend behavior for nftables/UFW/firewalld or explicit unsupported
  states;
- QML manifest validation and lint/static checks;
- FluxCast unit tests for supplicant, capture, media, RTSP, and cleanup.

Hardware test dimensions:

| Dimension | Minimum cases |
| --- | --- |
| Receiver | Proven Fire TV; one additional Miracast sink before broad claims |
| Radio | Supported single-radio topology; preferred two-radio topology |
| Mode | Mirror |
| Profile | Safe (1280x720p60 at 7 Mbps) |
| Content | Test pattern, desktop motion, browser video, live/replay video |
| Lifecycle | Normal stop, connect failure, capture failure, forced process death, logout |
| Network | Idle internet, sustained download/ping, stream service traffic |

Every hardware run should produce a session bundle containing structured
events, human log, relevant radio counters, negotiated WFD mode, and cleanup
result. Do not store Wi-Fi credentials, browser data, or unrelated system logs.

## 6. Release acceptance criteria

The first supported release is complete only when all are true:

- install, validation, enable, update, disable, and removal work through
  Omarchy 4's plugin commands;
- setup clearly reports missing or incompatible dependencies;
- the supported Fire TV is discovered by name without a hard-coded MAC;
- Mirror works at the declared default profile for three consecutive 30-minute
  sessions while internet remains usable;
- audio is present, remains acceptably synchronized, and motion is acceptable
  for the declared profile;
- the validated Safe profile is reported honestly; an incompatible
  negotiation fails visibly instead of silently selecting an unmeasured mode;
- Stop completes promptly and restores networking, firewall, capture, and
  display state;
- forced termination and logout recover without manual service restarts;
- the UI always reflects the actual state and does not claim a quality mode
  that was not negotiated;
- logs identify the failed stage and give an actionable next step;
- security review finds no arbitrary privileged execution surface and no
  unnecessarily broad persistent D-Bus/firewall policy;
- the fresh-clone test succeeds without `work/` or other untracked files.

A protected streaming service may be documented as a tested example only after
its explicit capture/DRM and 30-minute playback test passes.

## 7. Immediate next work

Release-candidate handoff (updated 2026-08-24): preserve the accepted 720p60 /
7 Mbps Matroska media and Fire TV transport defaults. Distribution and
independent acceptance remain required:

1. Publish this repository and its full patch series at a stable git URL.
2. Publish the companion Arch package through a trusted channel or provide a
   signed release artifact. Omarchy's plugin manager cannot install it.
3. Test `omarchy plugin add`, enable, Super+Alt+C summon, update, disable, and
   removal from a clean clone/account.
4. Complete repeated 30-minute 720p60 sessions and one forced-failure cleanup
   matrix on the supported topology before changing “release candidate” to
   “supported release”.
5. Validate real browser live/replay playback before claiming protected or
   online video compatibility. Portal source selection remains gated until its
   own tests pass.

### Production-finish backlog

The following work is explicitly retained for the production and competition
finish. Reliability items gate release; presentation items must remain thin
clients of the controller and must not weaken the accepted media path.

1. **Fix first-cast privilege and post-restart Wi-Fi readiness.** Reproduce a
   cold boot and a NetworkManager/wpa_supplicant restart, then prove that the
   first panel-launched cast needs at most one clearly explained polkit prompt.
   Eliminate manual creation or repair of `/run/systemd/network`, stale P2P
   interfaces, and ambiguous NetworkManager/systemd-networkd ownership. Report
   authorization cancellation, missing runtime setup, DHCP failure, and P2P
   negotiation failure as different actionable errors. Add failure-injection
   coverage and verify exact restoration of the service/socket states that
   existed before the cast.
   Implementation note (2026-08-23): the controller now carries stable codes
   for cancelled/timed-out authorization, helper setup, DHCP, P2P, receiver
   negotiation/timeout, capture, and generic engine exit through runtime state
   and session history. Offline tests prove cancellation is reported as making
   no change and prove code propagation. Live failure injection and exact
   network-state restoration remain required before this item closes.
2. **Replace the fixed duration with Cast Until Stopped.** The revision-26
   development implementation now defaults normal sessions to until-stopped,
   separates the 45-second P2P and 75-second connection clocks from session
   lifetime, and uses a renewable 60-second lease. It still requires a clean
   package build plus logout, death, suspend, helper-kill, and live receiver
   acceptance before this item closes. A normal panel cast
   must not expire after the controller's current five-minute default or the
   helper's 30-minute ceiling. Use a short, renewable, session-owned lease:
   the unprivileged supervisor renews it only while the engine, RTSP session,
   and ownership lock remain healthy; the independent privileged recovery path
   restores networking after a bounded missed-heartbeat window. Stop, logout,
   engine death, controller death, suspend, and a killed helper must all remain
   recoverable without turning the temporary network change into an unbounded
   privilege lifetime.
3. **Add an optional Nerd Mode.** Keep the normal live view friendly and compact,
   with a deliberate toggle for negotiated mode, actual cadence, capture/mux
   load and scheduler delay, RTP throughput/queueing, radio counters, A/V timing,
   and bounded session evidence. Nerd Mode must identify unavailable probes
   honestly and must not enable packet tracing or other measurement that can
   perturb the stream merely by opening the panel.
   Implementation note (2026-08-23): the native panel now defaults to four calm
   live rows and expands on demand into cadence, pacing, process load, scheduler
   delay, RTP, radio, packet/A/V timing, and peaks. Missing telemetry and the
   default-off deep packet probe are labelled explicitly. Both collapsed and
   expanded states were rendered in the real shell with a simulated session;
   receiver-populated values remain part of the next live acceptance run.
   Release cleanup shortens labels and values, reports only a health flag count
   instead of dumping issue strings, and keeps packet tracing off.
   Final UI review replaced the remaining row dump with a two-column metric
   grid and hides packet/A/V cards while their deep probe is unavailable.
4. **Research optional smooth-playback buffering without changing the release
   default.** A fixed one-second delay is not presumed to solve clock drift.
   Compare an explicit, reversible buffer-off baseline with a bounded adaptive
   jitter-buffer candidate. Promote a Nerd Mode option only if repeated motion
   tests show fewer visible cadence oscillations, stable A/V sync, and an
   honestly reported latency cost without weakening Stop or cleanup.
5. **Superseded: restore portal-selected casting as a separately gated mode.** Let the user
   choose an output, window, or region through the desktop portal rather than
   exposing an unrestricted path or compositor command to QML. Keep mirror as
   the only advertised release mode until portal capture passes the same
   receiver, audio, pacing, cancellation, privacy, and cleanup gates.
   Progress note (2026-08-23): controller diagnostics now perform three
   read-only D-Bus property queries for the ScreenCast interface version,
   standard source-type mask, and cursor-mode mask. The result distinguishes
   monitor, window, and virtual capabilities while labelling region selection
   as picker-dependent; it neither opens a picker nor changes release
   readiness. This establishes a capability-driven UI contract without
   claiming that portal capture has passed its separate acceptance gate.
   Superseded implementation note (2026-08-23): Super+C captured only bounded display
   labels for the active Hyprland window, then opens a contextual panel. The
   controller requires a window-capable portal and threads `source=window`
   through the detached service and session record. The first correctly
   targeted Fire TV run proved the transport but found that GSR rejects the
   portal's SHM fallback. Patch 25 now uses the existing GStreamer PipeWire
   reader, FFmpeg VAAPI Safe encoder, and receiver-proven output pacer instead.
   It requests one window, rejects monitor/virtual/untyped returns, and requires
   encoded-frame progress before streaming state. Receiver acceptance is still
   required before advertising window capture as supported.
   Product decision (2026-08-24): this entire mode was removed from production.
   Patches 24–25 and their dependencies are excluded from the shipped series;
   the files and evidence remain for research continuity.
   Retry note (2026-08-23): the first installed revision-32 attempt selected a
   separately advertised Samsung TV while the Fire TV was waiting, so it
   correctly failed at P2P group formation before portal capture. The panel now
   ignores incidental hover when positioning its keyboard cursor, filters peers
   with explicitly empty Wi-Fi Display IEs, sorts the validated Fire TV class
   before generic WFD labels, and reports this terminal condition as P2P
   negotiation rather than DHCP. The later correctly targeted Fire TV retry
   reached PLAY and exposed the SHM capture failure described above.
   Picker constraint (2026-08-23): XDPH 1.4.1 ignores the standard requested
   source types and launches its custom picker without a request-local source
   hint. Omarchy's preview picker exposes only a global `default_page`. Omacast
   will not mutate that global preference or automate the consent UI; it gives
   an explicit Windows-tab instruction and rejects every non-window result.
6. **Superseded: add a privacy-safe local cast preview.** Show a small, clearly labelled
   preview of the selected source before connection and, if measurement proves
   it harmless, while streaming. The preview must never be archived, must blank
   protected/unavailable content honestly, and must not add a second full-rate
   encode or enough GPU/CPU work to disturb the transmitted cadence.
   Progress note (2026-08-23): the panel now uses Quickshell's native
   `ScreencopyView` for one still frame of the active Wayland toplevel or focused
   output. The cursor is disabled, unavailable/protected content is labelled,
   and no image path or write process exists. The capture source is bound to
   null whenever the panel closes or start-pending/session state begins, which
   releases the compositor capture context before guarded networking or the
   media engine starts. Real-shell window rendering passed without QML or
   screencopy warnings; final receiver telemetry remains the non-interference
   acceptance gate.
   Product decision (2026-08-24): the preview was removed. The Source row now
   identifies the selected output without taking a compositor screenshot.
7. **Polish and verify the stateful bar icon.** The implementation already uses
   the theme foreground while idle, orange during setup, green only while
   streaming, and red for recovery. Exercise discovery, authorization,
   connecting, streaming, stopping, failure, recovery, shell reload, and stale
   ownership so the icon never claims a state the controller has not proved.
   Retain glyph/tooltip distinctions as well as color so state remains legible
   without relying on color perception alone.
   Progress note (2026-08-23): recovery now swaps the cast glyph for a warning
   glyph and every state family has an actionable tooltip. A fake transport
   failure rendered the red warning icon while the panel was closed, opened to
   the contextual Restore action, and returned to the idle cast glyph after
   recovery. The remaining scan/authorization/connect/stop transition matrix
   still belongs in installed revision-27 and live acceptance.
   Progress note (2026-08-23): scanning was missing from the bar's visual-busy
   predicate even though the panel hero was truthful. The installed plugin now
   turns both bar and panel cast glyphs orange during a real read-only scan, and
   tooltips name looking-for-displays, approval, connection, and network
   restoration instead of collapsing them into generic setup. A real-shell scan
   render passed with no QML warning and returned to one idle icon; the remaining
   receiver-backed authorization/connect/stream/stop matrix stays open.

Do not substitute the Phase 1 launchers for the package-owned controller and
helper, and do not enable packet-level tracing in normal production sessions.

No live network test should begin unless the user has confirmed the Fire TV is
waiting in Display Mirroring and understands any temporary network change.

## 8. Decision log

- **Protocol:** Miracast/WFD via FluxCast; no custom receiver implementation.
- **Initial sink:** stock Fire TV in mirroring mode, discovered by advertised
  Wi-Fi Display name rather than a user-entered MAC.
- **Capture:** Hyprland output via GPU Screen Recorder on the accepted display
  path. Portal/window capture is a superseded research artifact.
- **Encoding:** H.264/AAC through the accepted GSR/FFmpeg display pipeline.
- **P2P role:** Fire TV as group owner; laptop as client via direct supplicant
  backend on the proven hardware.
- **Product boundary:** QML UI -> unprivileged controller -> supervised session
  -> narrowly scoped privileged networking -> FluxCast.
- **Omarchy format:** root `manifest.json`, schema version 1, validated by the
  installed Omarchy CLI.
- **Reliability policy:** radio topology and internet coexistence gate all UI
  and release work.
- **Privileged cleanup policy:** normal and independent recovery attempt every
  safe restoration step even after an earlier failure, preserve root-owned
  recovery evidence whenever cleanup is incomplete, and never recursively
  remove an unexpected object from the user-writable marker directory. The
  independent owner must validate and acknowledge the protected session before
  any temporary network or D-Bus mutation begins.
- **Dependency policy:** tracked upstream/fork/package; never an ignored local
  checkout or runtime patching.
- **Engine packaging:** `packaging/arch/PKGBUILD` pins the researched FluxCast
  base revision and applies the tracked compatibility/timing patches at build
  time. It ships no persistent D-Bus, firewall, or root networking policy.
- **Release profile:** 1280x720p60 at 7 Mbps with the Fire-TV-proven wire pacer,
  zero mux delay, user-service CPU weighting, and measured 64 ms audio timestamp
  correction. There are no alternate production profiles.
- **Brand/shortcut:** marketplace name **Omacast**, stable plugin ID
  `hardie.omarchy-cast`, and documented Super+Alt+C summon binding that leaves
  Omarchy's stock Super+C Universal Copy shortcut intact.
- **Browser-video policy:** explicit real-world acceptance test, no DRM bypass,
  and no protected-service compatibility claim until proven.

Update this decision log whenever evidence changes an architectural choice.
Do not erase contradictory experiment history; mark it superseded and explain
why.
