# Omacast research log

## Goal

Prove that an unmodified Fire TV Stick can receive a reliable 1080p60 desktop
stream from this Omarchy/Hyprland laptop through Miracast/WFD. Phase 1 is a
technical spike, not yet the polished Omarchy plugin.

## Decision

Use **FluxCast in WFD/Miracast mode** as the streaming engine, with
`wf-recorder` for Hyprland capture. Do not build a receiver, sideload Fire TV
software, or implement Miracast ourselves.

Why:

* FluxCast documents WFD as its primary and release-ready protocol, with a
  `screen + audio capture -> H.264/AAC RTP -> Wi-Fi Direct + RTSP` pipeline.
* It explicitly supports Hyprland/wlroots capture through `wf-recorder`.
* The current Fire TV Stick 4K (2nd Gen) and 4K Max (2nd Gen) specification
  pages list Miracast as supported.

Sources:

* https://github.com/IlyaP358/fluxcast
* https://developer.amazon.com/docs/device-specs/device-specifications-fire-tv-streaming-media-player.html?v=ftvstick4kmax_gen2_16
* https://www.amazon.co.uk/gp/help/customer/display.html?nodeId=ToUjwyfVuffzVfbR6b

## Phase 1 scope

1. Check prerequisites with FluxCast's `--doctor`.
2. Bring the Fire TV into **Enable Display Mirroring**.
3. Discover it and establish one manual WFD session.
4. Validate a mirrored physical output at 1920x1080 / 60fps with audio.
5. Create a temporary Hyprland headless output and validate it as an extended
   display.
6. Capture logs and document the repeatable start/stop commands.

## Deliberately out of scope

* Automatic Fire TV entry into Display Mirroring.
* Remote-control input back to Linux.
* 4K, HDR, and HEVC tuning.
* A finished Omarchy menu, bar widget, service, or installer.
* Persistent configuration or changes to Fire TV.

Amazon's documentation says a Fire TV remote button exits Display Mirroring;
therefore a stock remote cannot be the Phase 1 controller for the Linux stream.

## Expected architecture

```text
Hyprland output (physical or HEADLESS-CAST)
  -> wf-recorder capture
  -> FluxCast: H.264 + AAC / RTP / RTSP
  -> Wi-Fi Direct (P2P)
  -> stock Fire TV WFD receiver
  -> HDMI / TV
```

For extend mode, Hyprland creates `HEADLESS-CAST`; FluxCast captures that
output. The user moves a window onto its dedicated workspace as they would onto
any second monitor.

## Preconditions and risks

| Area | Requirement | Current observation |
| --- | --- | --- |
| Session | Hyprland on Wayland | Confirmed. |
| WFD management | NetworkManager, `nmcli`, `iw` and a Wi-Fi adapter with P2P support | Confirmed with NetworkManager 1.58 and `p2p-dev-wlp58s0`. |
| Capture | `wf-recorder` and FFmpeg | Confirmed, including PipeWire audio. |
| Hardware encode | VAAPI or another supported encoder | H.264 encoders are available; the proven test-pattern path currently uses software `libx264`. |
| Sink | Fire TV Display Mirroring screen, initially at 1080p | User must place it in receiving mode. |

## Acceptance criteria

Phase 1 passes only if all of these are true:

* Fire TV appears as a WFD peer while waiting in Display Mirroring mode.
* A stream runs continuously for 10 minutes with no manual reconnection.
* Audio is present and video is acceptably smooth at 1080p60.
* Starting and stopping leaves Wi-Fi and the regular desktop usable.
* A headless output can be streamed as an extended display.
* The process produces useful logs when a connection fails.

## Live test log (2026-08-20)

### Passed

* `fluxcast --doctor` found all core dependencies, Hyprland capture, PipeWire
  audio, NetworkManager P2P, and Wi-Fi Display support in `wpa_supplicant`.
* The household Fire TV appeared by its advertised personal label and radio
  address with WFD capability data. Both identifiers were redacted before
  public distribution; this does not change the discovery result.
* NetworkManager established a P2P group in both role configurations:
  source low group-owner intent (`0`) and source high intent (`15`).

### Failed before media

In both role configurations, Fire TV remained on “Preparing to mirror the
display from your device”. The laptop opened its RTSP listener on port 7236,
but the Fire TV never connected. FluxCast also could not discover a peer IPv4
address on the active P2P interface, so its source-initiated RTSP fallback could
not run. No desktop capture or video encoding began.

This records the original NetworkManager-backend result. It was superseded by
the direct-supplicant test below.

### Reproduced handshake timeline

The first attempt reached a complete WPA four-way handshake. The Fire TV joined
2.68 seconds after the WPS group started, but the unpatched NetworkManager
shared network was `10.42.0.1/24`; the receiver never requested DHCP and never
opened RTSP.

FluxCast was then patched locally to request shared IPv4 at
`192.168.49.1/24`. This reliably produced the expected DHCP range
`192.168.49.10-254`, but subsequent attempts exposed two fixed deadlines:

1. NetworkManager 1.58 removes a requested P2P peer after five seconds when its
   supplicant `Groups` property does not yet contain the new group. The value is
   hard-coded in `nm-device-wifi-p2p.c` and has no configuration key.
2. Pausing NetworkManager proved that `wpa_supplicant` then removes the group
   independently. Fire TV retries association about 9.6 seconds after WPS, but
   upstream `wpa_supplicant` 2.12 allows only ten seconds for the first data
   connection on a newly negotiated group owner. The retry reached the laptop,
   but the four-way handshake did not finish in the remaining ~0.4 seconds.

The latter maps directly to upstream
`P2P_MAX_INITIAL_CONN_WAIT_GO`, whose default is 10 seconds. It is guarded by
`#ifndef`, so a test binary can safely override it at build time. A local Arch
2.12-compatible binary was built with
`-DP2P_MAX_INITIAL_CONN_WAIT_GO=30` and tested through a temporary, automatically
restored runtime service override.

With that binary, Fire TV completed `AP-STA-CONNECTED` and
`EAPOL-4WAY-HS-COMPLETED`. This proves WPS credentials, WPA authentication, and
the Wi-Fi radio path are compatible. NetworkManager nevertheless continued to
consider the peer absent from the supplicant `Groups` property and destroyed
the authenticated group when its own timer resumed.

Holding NetworkManager after activation allowed FluxCast to enter its RTSP
phase. During that window Fire TV sent no DHCP request, created no neighbour
entry, and made no passive RTSP connection. A patched active probe explicitly
tried the conventional static client address `192.168.49.2:7236`; ARP received
no answer and the connection failed with `No route to host`.

### Direct-supplicant breakthrough

A local FluxCast backend was added for direct
`fi.w1.wpa_supplicant1.Interface.P2PDevice.Connect` control. With
`go_intent=0`, Fire TV won negotiation and the laptop joined as a P2P client.
The successful group had:

* Fire TV as group owner and laptop as client on channel 1;
* a stable WPA-encrypted link around 130–144 Mbit/s in the observed run;
* a DHCP lease supplied by Fire TV (the private subnet and observed addresses
  change per session and are redacted from the public record); and
* Fire TV's group-interface MAC represented in the neighbour table.

NetworkManager 1.58 destroys groups created directly behind its back, so the
diagnostic briefly pauses NetworkManager after discovery. A narrowly matched
temporary `systemd-networkd` configuration supplies DHCP only on
`p2p-wlp58s0-*`. Both changes are guarded and automatically restored.

The first complete media attempt exposed a separate host issue: UFW was active
with default-deny incoming and silently dropped TCP 7236. Opening TCP 7236 only
on the P2P interface allowed Fire TV to connect to FluxCast's RTSP server.

The successful run completed WFD M1 through M5, followed by Fire TV `SETUP` and
`PLAY`. It negotiated 1280x720p30, H.264 baseline, and AAC, then transmitted RTP
continuously. FluxCast reported first RTP bytes 703 ms after `PLAY`, successful
M16 keepalives, and increasing transmitted bytes. The user confirmed visible
test video and audible audio on the stock Fire TV.

### Coexistence soak and radio constraint

The temporary policy, DHCP client, P2P-only firewall rule, NetworkManager pause,
timeout, and cleanup are now controlled by one guarded launcher and one
administrator approval. Discovery triggers the disruptive portion only after
the requested peer has been found. Cleanup was exercised after discovery
failures, rejected negotiation, manual cancellation, and a running media
session; the normal network, D-Bus policy, firewall, and services were restored.

This laptop's adapter advertises two-channel managed/P2P-client concurrency,
but the band/channel combination matters in practice:

* With ordinary Wi-Fi on channel 149 (5745 MHz) and Fire TV P2P on channel 1
  (2412 MHz), the P2P client suffered beacon loss and disconnected after about
  eight seconds.
* Asking Fire TV to use 5745 MHz failed explicitly with
  `ConnectChannelUnsupported`; its mirroring receiver did not offer that
  operating channel.
* Moving the existing Wi-Fi connection temporarily to its 2.4 GHz access point
  on 2442 MHz, while allowing Fire TV to choose 2412 MHz, produced a sustained
  concurrent internet and Miracast session.

The final soak was stopped at the user's request after more than six minutes of
continuous RTP rather than the planned ten. It transmitted over 212 MB, held
the RTSP TCP session throughout, and showed zero TX retries, zero TX failures,
and zero interface drops. The P2P signal was about -55 dBm with a negotiated
117–144 Mbit/s transmit rate. One `CTRL-EVENT-BEACON-LOSS` appeared near the
end, but it did not remove the group or stop media before manual cancellation.

The user reported slight visible stutter. Since the radio counters were clean
and the synthetic `testsrc2` plus `libx264` sender consumed roughly 1.6 CPU
cores, treat this as an unresolved frame-pacing/encoding issue rather than a
connection failure. Hardware H.264, capture timing, bitrate, and receiver
buffering still need to be tested with the real desktop path.

### Real Hyprland desktop and hardware encoding

FluxCast's monitor discovery was found to be X11-only even in a Hyprland
session. A Hyprland path now reads `hyprctl monitors -j`, allowing detached
sessions to select `eDP-1` without changing Hyprland configuration. The
wf-recorder sender also gained explicit software/VAAPI selection.

A local smoke test successfully captured the 1920x1080 Hyprland output with
`wf-recorder`, scaled it to 1280x720, uploaded NV12 frames to
`/dev/dri/renderD128`, and encoded constrained-baseline H.264 with Intel VAAPI.
The end-to-end Fire TV run then completed P2P, DHCP, RTSP, and started this real
desktop pipeline with AAC audio. The user confirmed that the desktop appeared
on the TV.

That run ended after roughly 12 seconds because the P2P client logged beacon
loss, disconnected, and was removed. This happened while ordinary Wi-Fi used
2442 MHz and Fire TV used 2412 MHz. It proves capture and hardware encoding are
compatible, but also confirms that two-channel concurrency on this adapter is
not reliably repeatable: a prior identical radio arrangement lasted more than
six minutes, while this one lasted only seconds.

### Updated interpretation

The stock Fire TV, Wi-Fi adapter, FluxCast RTSP implementation, H.264 encoder,
AAC encoder, and RTP path are compatible. The blocker is specifically
NetworkManager's P2P role/lifecycle abstraction on this machine, plus local
DHCP and firewall integration when bypassing it.

### Guarded mirror confirmation (2026-08-21)

The consolidated guarded launcher was exercised again with the Fire TV in
Display Mirroring mode. These runs used the compatible single-radio topology:
the existing infrastructure profile was temporarily pinned to its 2.4 GHz AP
at 2442 MHz, while the Fire TV negotiated its P2P group on channel 1. The pin
was removed and the original band-steering behaviour restored after each run.

* A two-minute 1280x720p30 synthetic test pattern was visible on the Fire TV.
* A five-minute 1280x720p30 real Hyprland `eDP-1` capture using `wf-recorder`
  and VAAPI remained running for the complete requested window. The user found
  it readable and visually good, with only the small end-to-end delay expected
  from Miracast.
* The guarded launcher removed its temporary D-Bus policy, P2P-only firewall
  rule, DHCP configuration, and token files, resumed NetworkManager, and
  reconnected the normal Wi-Fi profile after both runs.

This supersedes the earlier claim that the 2.4 GHz real-desktop path had only
reached about twelve seconds: it is now demonstrated for five minutes. It does
not yet establish a supported single-radio topology. The acceptance plan still
requires repeatable ten-minute runs with concurrent internet traffic and
production-quality telemetry before a one-click plugin control may make these
network changes.

### Packaged controller confirmation (2026-08-21)

The reproducible `fluxcast-omarchy-cast` package and the production controller
were exercised end to end, rather than through the research launcher. The
controller issued a session-specific `pkexec` helper request, FluxCast created
the guarded trigger, and the helper created a Fire-TV-owned P2P client on
channel 1. The laptop received a private DHCP lease; the Fire TV appeared at a
different private address and established TCP RTSP to the FluxCast listener on
7236. The observed addresses are redacted from the public record.

The real `eDP-1` desktop and audio played on the TV for the complete requested
five-minute window. The user confirmed sound, visible desktop, noticeable
Miracast latency, and stuttering. This attempt retained ordinary infrastructure
Wi-Fi on 5 GHz channel 149 while P2P used 2.4 GHz channel 1. It therefore
confirms the packaged lifecycle and cleanup, but is evidence *against* calling
this topology smooth or generally supported. At expiry, FluxCast and both guard
processes exited, the controller returned to idle, NetworkManager was active,
and the original Wi-Fi profile was connected again.

### Instrumented release-candidate tuning (2026-08-22)

Subsequent work moved the real desktop path to GPU Screen Recorder, added
one-second end-to-end telemetry, and measured capture, mux, RTP transport,
socket queueing, packet cadence, radio counters, scheduler delay, and audio/
video timing. Packet-level framecrc tracing remains opt-in because its second
FFmpeg output and continuous disk I/O can perturb the pipeline being measured.

The experiments rejected several superficially plausible fixes—including the
1080p stress profile and transport timing variants that worsened audio or video
cadence. The accepted candidate restores the Fire-TV-proven 120% batched wire
pacer, zero mux delay, scheduler priority for the owned media process tree, and
a measured 64 ms GSR audio timestamp correction. Its release profile is
1280x720p30 at 7 Mbps. The user described the final run as dramatically better
and then accepted the result for plugin completion; minor perceived audio lag
was noted immediately before acceptance, so this is not evidence for a
"flawless" or broadly supported sync claim.

This evidence supersedes the earlier statement that the packaged path was only
known to be badly stuttery. It does not authorize a 1080p, Sports, multi-sink,
or general-hardware claim. Preserve the accepted defaults and use measurements
to evaluate future changes rather than restarting manual tuning from scratch.

The production discovery adapter was also exercised on the live radio on
2026-08-22. It returned an unvalidated WFD television and the validated Fire TV
by their advertised Wi-Fi Display names, with private labels redacted and
normalized peer IDs kept inside the controller. This closes the manual-MAC UI
gap without claiming that the unvalidated receiver supports the complete
streaming profile.

The production launcher was then moved into the named transient user service
`omacast-session.service`. A no-hardware simulated streaming session remained
active, with the same owner PID and state lock, across an actual
`omarchy restart shell`. A second simulated session was deliberately killed
with `SIGKILL`; status detected the active-state/unowned-lock mismatch, the
panel rendered a red **Restore casting state** action, and recovery returned it
to idle. This validates shell independence and the local recovery UX without
claiming new receiver/network evidence. Cancellation is also checked while the
administrator authorization helper is still pending.

The session service was subsequently wrapped in a process-owned logind
inhibitor for `sleep:idle`, while the Omarchy widget gained a Wayland idle
inhibitor attached to the persistent bar. In an offline simulated streaming
session, `systemd-inhibit --list` reported Omacast with reason “Desktop casting
is active”. A normal panel-equivalent Stop removed the inhibitor immediately.
A second session was killed with `SIGKILL`; the inhibitor again disappeared
before state recovery, proving that this integration cannot leave a persistent
stay-awake override behind. The same run confirmed the red owner-lost recovery
path and return to idle. Session age is now explicit in state/UI, and private
event history is capped at 50 sessions.

The original hard-coded Phase 1 launchers are now grouped under `scripts/lab/`.
Their internal paths and syntax were revalidated, but they remain evidence only
and are not called by the marketplace plugin or production controller.

The release audit also found that `scripts/bootstrap-fluxcast` still applied
only the first three historical patches while the Arch package applied all 22.
Both paths now consume one validated `patches/production/series` file. A separate checkout
was recreated from pinned base `9d27c39`; all 22 commits applied in order, the
resulting tree was clean, and `git diff --check` passed. The temporary checkout
was then moved to the desktop trash. This closes the local source-reproduction
gap without making a new hardware claim.

The full companion package was subsequently built from a clean clone of release
commit `f7465fc`, rather than from the development working tree. Makepkg applied
the same 22 patches, passed all 96 engine tests, and emitted
`fluxcast-omarchy-cast 0.1.5.r3.omarchy-24` (472142 bytes; SHA-256
`0d446bfa4816cc8a6181132e87105f21b6032b2e6b7bd2969d21a8d733f18fad`).
Package metadata and the packaged `fluxcast`, guard, and recovery executables
were inspected, while the installed package independently passed `pacman -Qkk`
with 191 files and zero alterations. This verifies the local build artifact; it
does not substitute for publishing or signing a trusted release artifact.

Release automation was then added around that recipe. The builder refuses a
dirty source tree, clones the exact commit, uses a locked owner-checked fixed
build root, and emits the package together with its checksum, package metadata,
and source commit. Two independent host builds of commit `c0eab756` were
byte-identical (`9930794460dd6779f4fbe9620a88177e39fad0b4f6baf1730b59d8186a2caa4e`).
The GitHub-workflow command was also exercised locally in a fresh official Arch
`base-devel` container. It exposed and then closed a headless `pystray` test
environment bug; the corrected build passed all 96 engine tests and emitted a
checksum-valid artifact bound to full source commit
`c0eab7560390c4bf20d1790a98b308a163fb735b`. Its container hash was
`1b1cb8c6d662b905a09d3f0826ed8043b3d4aa7185908469dcbbbd1a1cec701b`.
The container and host hashes differ because Arch package `.BUILDINFO` records
the installed dependency set. Tagged public workflows will add GitHub artifact
provenance so users can verify the exact hosted builder and commit; that hosted
attestation remains pending a public repository and tag.

### Long-session cadence and lifecycle evidence (2026-08-23)

A package-owned real-desktop cast ran for about 20.5 minutes and transmitted
796,513,176 bytes before an ordinary user-approved Stop. The retained telemetry
contained 1,234 samples: 1,133 healthy, 61 attention, and 40 warming. Measured
frame rate had a 30 fps median, 27.67 fps first percentile, and 26 fps minimum;
17 samples were below 29 fps. Capture scheduling delay peaked at 72.4 ms/s and
the largest recorded queue was 34,560 bytes. The radio recorded no retries,
failures, beacon loss, or interface drops, and the mux reported no dropped or
duplicated frames. Teardown removed the P2P device and temporary network state,
returned the controller to idle, and left NetworkManager and infrastructure
Wi-Fi active.

The user nevertheless observed a roughly 100 ms, almost periodic video stutter.
Cadence dips appeared in several clusters roughly 3.5 to 5.25 minutes apart.
Only one cluster coincided with a kernel `perf: interrupt took too long` event,
so that event cannot explain the complete pattern. The retained one-second
telemetry also cannot distinguish encoder cadence, mux/RTP pacing, receiver
playout, or sampling aliasing by itself.

An offline, no-network probe then exercised the exact GPU Screen Recorder CFR
capture mode into a local null sink for about 200 seconds. GSR's update counter
was always 30 or 31 in each one-second sample (86 samples at 30 and 111 at 31),
while FFmpeg progressed at about 30.04 fps. This does not reproduce the visible
problem and weakens the hypothesis that the raw GSR capture loop is the sole
cause. It does not clear the later mux, RTP, radio, or receiver stages. No media
pacing default changed from this evidence; the next isolation should measure
loopback RTP arrival gaps without retaining frames, followed by receiver-side
validation when the TV is available.

That loopback isolation was then completed against the exact accepted 720p30
sender arguments. Over six minutes, the GSR-to-Matroska-to-FFmpeg path delivered
62,237 RTP datagrams with zero missing sequence numbers, but produced 6,762
arrival gaps at or above 20 ms, 3,624 at or above 50 ms, and 234 at or above
100 ms. The largest gap was 127.84 ms. A separate one-minute metadata pass saw
only about 18.95 RTP timestamp changes per second and 66 timestamp-change gaps
at or above 100 ms. Because this occurred entirely on loopback, Wi-Fi and the
Fire TV are not necessary to reproduce the burst delivery.

FluxCast's moving FFmpeg test-pattern pipeline was the control. In one minute
it sent 42,619 datagrams with zero sequence loss and no packet or timestamp-
change gap at or above 50 ms. Replacing only the internal GSR pipe container
with FLV, while keeping H.264, AAC, receiver-facing RTP/MPEG-TS, mux rate, wire
pacer, and burst allowance unchanged, reduced the one-minute result to eight
packet gaps at or above 50 ms, two timestamp-change gaps at or above 75 ms,
and none at or above 100 ms. This strongly isolates the continuous microstutter
to batching in the current GSR Matroska handoff rather than the raw capture
counter, radio, or RTP pacer alone.

Patch 23 packages FLV as an explicit `--wfd-gsr-handoff flv` candidate and
keeps Matroska as the default. The controller exposes it only through the
diagnostic `OMARCHY_CAST_GSR_HANDOFF=flv` environment value. The reconstructed
engine passed 97 offline tests. FLV must still complete a controlled live A/B,
audio-sync check, and teardown run before it can replace the receiver-proven
default.

The complete dirty-tree development package was then built as
`fluxcast-omarchy-cast 0.1.5.r3.omarchy-26`. Makepkg applied all 23 patches,
passed the same 97 engine tests, and produced SHA-256
`121758cd3d5d7bd4366f0fc95f26708db9408d2827b956bdfbac3d7c459a42ef`.
The archive contains both guard helpers and the handoff implementation. This
is package-integration evidence only: it was not produced by the clean release
builder, installed, or used for a live cast.

After committing the implementation as `320c0f8`, the exact-clean release
builder cloned that source commit, reapplied all 23 patches, passed 97 engine
tests, and emitted revision 26 with SHA-256
`336659ea5eade1033fb54b062bec607be0c6b86c7508c8f1b9422f9c15704182`.
`SHA256SUMS` verifies and `SOURCE-COMMIT.txt` records full source identity
`320c0f84ce0f2fbdf70b600f397517356cb61a3e`. Installation, upgrade, privileged
failure injection, and receiver validation remain pending.

The subsequent compatibility audit found that executable presence and three
generic engine flags were insufficient to keep marketplace UI and the
privileged helper in lockstep. Package revision 27 adds an unprivileged JSON
guard-version probe at API revision 2, while readiness also requires patch 23's
engine flag. Against the real revision-24 installation, development `doctor`
now reports both engine and helper incompatibility and routes the panel to its
normal update-companion state before discovery.

The exact-clean builder was most recently rerun from implementation commit
`b71606390633586786c5435bfeea375cdc0c00d0`, applied all 23 patches, passed 97
engine tests, and produced revision 27 with SHA-256
`9d1173e6fe5d3d0563b0eedd8cf86cdf79f3631b7a5cbede6d5f2a6a9bb2e0c4`.
The checksum verifies; the helper extracted from that archive reports the
expected version document and both helper scripts pass `bash -n`. The archive
has not been installed because this session does not have a cached sudo
credential, and no surprise authorization prompt was opened.

A repeatable no-root audit now additionally rejects unsafe archive paths or
install scripts, checks required runtime dependencies and executable modes,
runs the extracted guard API probe, proves the packaged engine exposes the
required handoff/trigger/telemetry flags, and asks pacman to verify archive
integrity. It passed against the artifact above; this narrows but does not
replace the pending real install/upgrade and privileged lifecycle tests.

The package-manager lifecycle was also exercised without privilege in a fresh
fakeroot/pacman root. Revision 25 installed with 191 files and zero alterations;
revision 27 replaced it in place, again with 191 files and zero alterations,
and its installed helper reported API revision 2. Removing revision 27 left no
package-owned files or symlinks below the disposable `/usr`. The tracked
`scripts/test-package-lifecycle` reproduces this check for any older/newer pair.
This proves archive replacement behavior, not live dependency resolution,
polkit integration, or system networking behavior.

Before public distribution, the household receiver's advertised personal label
and radio address were redacted from documentation, fixtures, the retained
patch test, and lab launchers. The lab launchers now require explicit receiver,
Wi-Fi interface, and output inputs, preserving the experimental sequence without
publishing workstation identity. Because the retained patch changed, the recipe
advanced honestly to revision 28. The exact-clean builder used commit
`3dc69a086693f3692dee347963ed4e18252e06dc`, applied all 23 patches, passed 97
engine tests, and emitted SHA-256
`62b3233f8dcb0a2ddcfefd1fbced8b3a8d020272e694023cee3d3f00226bec50`.
The no-root artifact audit and revision-27 to revision-28 disposable pacman
upgrade, integrity, helper-contract, removal, and orphan checks all passed.

Marketplace requirements were refreshed against upstream commit
`c9f6a5edc11b47dc450d3e7f5023b768fba62d5d`. The six-heading submission contract
and five owner assertions remain unchanged, and `hardie.omarchy-cast` was absent
from both active and retired registry IDs. The same commit's baseline-v3 logic
was most recently run locally against post-redaction Omacast commit `d9d5f70`:
it scanned 42 relevant
files, found no blocking or non-blocking unsafe patterns, and returned the
expected `review-required` disposition with `blocksApproval: false` for the
documented privilege, package-management, and service-management capabilities.
This local exact-logic result is not the official result: upstream deliberately
requires a public GitHub snapshot and a commit-bound maintainer decision.

The optional Nerd Mode was then implemented strictly in QML over the existing
one-second controller snapshot. The default live view now shows only display,
session, quality, and health; an explicit toggle reveals cadence, pacing,
capture/mux load, scheduler delay, RTP, radio, packet/A/V timing, and maxima.
It does not set `OMARCHY_CAST_PACKET_TELEMETRY`, start another process, or read
an additional file. Real-shell simulated renders verified the compact and
expanded layouts at the production panel width. Missing telemetry reads as
waiting/unavailable rather than zero-valued evidence, and the packet section
states when its deeper, perturbing probe is off. A normal Stop returned the
simulation to idle with no user service left active.

The stateful-icon recovery path was also exercised without hardware. A fake
transport failure left the controller intentionally in `error`; after the
closed-panel heartbeat, the bar showed an urgent warning glyph rather than a
color-only cast glyph. Opening the panel showed the same warning symbol, “CAST
NEEDS ATTENTION”, the bounded failure detail, and the contextual Restore action.
Recovery returned the controller and icon to idle with no user service or media
process active. The full live transition matrix remains open.

The controller failure contract was then made specific without changing the
guard or radio. Dismissed and timed-out authorization, guard setup, DHCP, P2P,
receiver negotiation and its bounded timeout, capture, and generic engine exit
now have stable codes. A focused cancellation fixture verifies the user-facing
claim that nothing changed, while a supervisor fixture proves that a concrete
code survives into both runtime state and bounded session history. The complete
suite passes 99 tests. This is offline contract evidence only: actual helper,
DHCP, P2P, and receiver failure injection remains part of acceptance.

The same investigation found that Omacast had incorrectly passed the requested
session duration to FluxCast's `--wfd-supplicant-hold` option. In the retained
engine patch this option bounds P2P group formation, not stream lifetime. That
coupling explains why a five-minute panel session or 30-minute manual session
could appear stuck in negotiation for the same interval. The development
controller now uses a dedicated 45-second group-formation timeout, a 75-second
overall connection deadline, and an independent renewable 60-second privileged
lease for normal until-stopped sessions. This is offline-tested revision-26
work, not yet installed or validated against the Fire TV.

Finally, an offline simulated session confirmed that the bar icon previously
missed controller changes while its panel was closed. A lightweight three-second
closed-panel status heartbeat made the icon turn green without opening the
panel and return to the idle theme after Stop. This verifies shell presentation
state only; the remaining transition matrix still needs deliberate exercise.

### Remaining acceptance work

The end-to-end feasibility proof has passed, but Phase 1's full acceptance gate
has not. Next:

1. Complete the remaining repeatability matrix on 2.4 GHz: at least three
   ten-minute test-pattern runs and three ten-minute real-desktop runs while
   ordinary internet traffic remains active; investigate any beacon-loss
   notification.
2. Resolve radio concurrency, most likely with a second Wi-Fi adapter or by
   placing the infrastructure AP on Fire TV's channel 1.
3. Validate 1080p, extend mode, and portal source selection separately; none
   may alter the accepted 720p30 release profile before passing its own gate.
4. Decide whether to upstream the direct backend to FluxCast or retain the
   pinned, reproducibly packaged compatibility patches.

## What the plugin implementation now includes

The repository now packages the workflow as a third-party Omarchy plugin:

* `manifest.json` plus a native bar/panel entry point;
* installation and capability checks;
* live device discovery and connect/stop actions;
* measured mirror mode with integrated runtime telemetry; and
* a supervised session controller that owns FluxCast and restores temporary
  network state.

The earlier Extend and multiple-profile plan is superseded by the production
scope consolidation below. Portal source selection remains future, separately
gated work.

Omarchy supports third-party plugins as Git repositories containing a root
`manifest.json`, installed with `omarchy plugin add <repository>`.

### Local Omarchy lifecycle verification (2026-08-22)

The actual installed Omarchy CLI and running shell were exercised without
removing or rewriting the production plugin. A temporary clone changed only
the manifest identity/name/version to a unique lifecycle-test ID, then ran the
real add-with-enable, official validator, fast-forward update, disable,
explicit enable, disable, and remove commands. The installed test checkout
advanced to the exact new source commit, disappeared from both disk and the
shell registry after removal, and left the live `shell.json` byte-identical to
its initial value. The production `hardie.omarchy-cast` registry record was
identical before and after. The temporary repository was moved to desktop
Trash by the cleanup trap.

This validates the local manager/shell lifecycle and rollback cleanliness. It
does not close the stricter release gate requiring the permanent public URL and
a genuinely clean Omarchy account.

### First-run and launch-race hardening (2026-08-22)

The controller now owns one complete readiness verdict covering the compatible
engine, both immutable guard helpers, required commands, connected Wi-Fi,
Hyprland source, and PipeWire audio. The panel does not scan until this verdict
is ready. A missing companion produces one visible-terminal setup action; it
does not silently install or elevate.

An installed-controller race test then exposed two distinct cancellation
windows: systemd may accept a transient unit before it publishes runtime state,
or the process may publish `checking` immediately before Stop arrives. The
final controller handles both. Idle-state Stop cancels and waits for the unit,
then safely sweeps any state published during cancellation; `checking` may
transition directly to `stopping`. Five consecutive immediate simulated
start/cancel cycles covered both timings and each ended idle with no transient
service or sleep inhibitor. This is lifecycle evidence only and does not claim
new receiver or radio acceptance.

### Read-only portal capability baseline (2026-08-23)

With the receiver unavailable, the installed desktop portal was queried only
through ScreenCast `get-property` calls. It reports interface version 6,
available-source mask 7 (monitor, window, and virtual), and cursor-mode mask 3
(hidden and embedded). The controller now exposes the same bounded probe in
`doctor` output and degrades to an optional unavailable result when D-Bus or the
interface is absent. Tests assert that exactly three property reads occur and
that no picker or ScreenCast session method is invoked.

This does not establish window or virtual capture compatibility in FluxCast.
Region selection is not a standard advertised source bit and is recorded as
picker-dependent, not supported. Mirror remains the only advertised mode until
portal video, audio, pacing, privacy, cancellation, and cleanup pass their own
receiver-backed acceptance gate.

### Independent telemetry cleanup and revision 29 (2026-08-23)

A receiver-free review of the privileged fallback found that normal controller
teardown removed the fixed set of volatile live telemetry files, while the
independent missed-heartbeat recovery path could leave the media QoS marker
and session telemetry directory behind after controller death. Revision 29
now removes only the six known filenames (`current.json`, FFmpeg progress,
packet timing, engine latency/log, and `qos.pid`) and then attempts a
non-recursive removal of the empty session directory. Caller-provided paths,
globs, and recursive deletion are not accepted.

The exact-clean builder reconstructed revision 29 from commit `f3c2faf`,
applied all 23 FluxCast patches, and passed 97 engine tests. The no-root artifact
audit, candidate-only disposable install/removal, and revision-28 to
revision-29 disposable upgrade/removal lifecycle all passed. The artifact
SHA-256 is
`dbe160b213a5dc49bd809f6f2bd825eac36def21f0405956d7bfdc2526512259`.
This is offline package evidence; killing a live privileged owner and verifying
the exact runtime/network state still belongs in receiver-backed acceptance.

### Current-tree marketplace preflight (2026-08-23)

After the portal capability probe, revision-29 cleanup, and executable-profile
restriction, the marketplace baseline-v3 logic from upstream commit
`c9f6a5edc11b47dc450d3e7f5023b768fba62d5d` was rerun against exact Omacast
commit `e3dce26`. It scanned 42 relevant files, found zero blocking or
non-blocking unsafe patterns, and returned the expected `review-required`
disposition with `blocksApproval: false`. The declared capabilities remain
privilege, package management, and service management.

This refresh does not replace the official public GitHub snapshot or maintainer
review. It confirms only that the newly added read-only D-Bus probe, bounded
recovery cleanup, and CLI restriction did not introduce a baseline finding.

### Production scope consolidation and revision 30 (2026-08-23)

The user explicitly retired the experimental extended-display and alternate
quality-profile product branches. Production planning, probes, simulation,
state validation, and execution now expose one coherent contract: mirror the
selected output with the receiver-accepted Safe 1280x720p30 profile. The old
per-user defaults module was removed because there is no longer a meaningful
mode/profile choice to persist. Historical experiments remain evidence, not
latent production controls.

An offline ShellCheck pass also found an ambiguous final dispatch expression in
the privileged guard: a failed `prepare` call could fall through to `stop`.
Revision 30 replaces that and similar boolean chains with explicit control flow
and adds ShellCheck for all production shell surfaces to release CI. The
retained lab scripts remain frozen research artifacts and are deliberately
outside this production lint gate.

The exact-clean builder reconstructed revision 30 from commit `ee522b8`,
applied all 23 FluxCast patches, and passed 97 engine tests. The no-root artifact
audit, candidate-only disposable install/removal, and revision-29 to revision-30
upgrade/removal lifecycle passed. The artifact SHA-256 is
`40d77709b9fc2c17fd1053f69c0656f5d406c2a365dbcbf0e37ca02dd9085a3f`.
The complete controller and packaging suite also passed 102 tests plus the
official Omarchy validator. The current marketplace baseline-v3 logic scanned
42 relevant files from that exact commit, found zero unsafe patterns, and
returned the expected `review-required` result with `blocksApproval: false`.
Receiver-backed and live privileged acceptance remain deliberately pending.

### Contextual window source and revision 31 candidate (2026-08-23)

The user identified two interaction failures in the production panel. Receiver
discovery implicitly selected the first TV, so Cast could appear without an
intentional destination choice; Super+C merely toggled a generic panel and
ignored the active window. The panel now clears stale receiver identity on scan
failure and rescan, never auto-selects a discovered receiver, and hides the Cast
action until a mouse selection exists. Its native keyboard cursor highlights
the first result without selecting it: ↑/↓ changes the destination and one Enter
selects and starts it.

GPU Screen Recorder's `focused` source was investigated and rejected because
its installed manual documents that source as X11-only. On Wayland, revision 31
instead adds a tracked FluxCast patch for GSR's desktop-portal source. Super+C
queries `hyprctl activewindow -j` through the unprivileged controller, exposes
only bounded app/title/monitor labels to QML, and retains no address or PID. The
portal remains the authority: after receiver connection it asks the user to
confirm the window. Display and window requests share Safe 720p30, the measured
GSR/FFmpeg handoff, session ownership, telemetry, and cleanup. All 98 patched-
engine tests and 109 controller/plugin tests pass offline. Receiver-backed
window video, audio, pacing, picker cancellation, privacy, and teardown remain
the final acceptance boundary.

The exact-clean revision-31 build used source commit `66ab7e4`, applied all 24
patches, and emitted
`fluxcast-omarchy-cast-0.1.5.r3.omarchy-31-any.pkg.tar.zst` with SHA-256
`637f96439e5da63a4ef86300a245cd17f30da2054ec9cd7c3c23e7bfc094af6d`.
The no-root artifact audit, candidate-only disposable lifecycle, and revision-30
to revision-31 upgrade/removal lifecycle passed. A temporary unique-ID plugin
twin used a local fake controller in the real Omarchy shell: it loaded without
QML errors, displayed two inert receiver fixtures without selecting either,
and one Enter issued exactly one `start` request for the highlighted receiver
with `--source window`. The twin was then removed; the plugin directory was
absent and `shell.json` matched its pre-test snapshot byte-for-byte. This was a
UI contract test only and did not scan, elevate, open a portal picker, or cast.

Marketplace baseline-v3 was then rerun against exact evidence commit `4ceef58`.
It scanned 42 relevant files, found no unsafe patterns, and returned the
expected `review-required` disposition with `blocksApproval: false`; the only
capabilities remain privilege, package management, and service management.
This local preflight does not replace the final public-snapshot marketplace
scan or maintainer review.

### Repeat-cast networkd ownership failure and revision 32 candidate (2026-08-23)

The first installed revision-31 receiver run negotiated and streamed the Safe
display path successfully. After warm-up, telemetry reported 30.33 measured
fps, 0.996 realtime ratio, zero dropped or duplicated frames, zero radio retries
or failures, and an empty send queue. Normal Stop returned the controller to
idle, removed the user service and inhibitor, restored infrastructure Wi-Fi,
and returned the pre-existing P2P client interface to down/unmanaged state.

The immediately following keyboard-first window attempt proved that one Enter
correctly carried `source=window` into the detached production session, but its
guard failed before network activation with `systemd-networkd is already
active`. A read-only `networkctl` query after the prior cleanup had
socket-activated the service; that is a legitimate host state, not conflicting
ownership. The error path then attempted to terminate the root-owned helper PID
from the unprivileged supervisor, received `EPERM`, and masked the stable
`guard-setup-failed` error. No session network file, inhibitor, or active cast
remained, and explicit recovery returned the controller to idle.

Revision 32 removes the blanket service-active rejection. The guard snapshots
all relevant networkd unit states as before; if the service is running it
reloads the new session-scoped match instead of trying to own or restart the
service, and normal/independent cleanup reload after removing that match before
restoring the recorded unit states. The supervisor always signals the
session-owned stop marker first and treats `EPERM`/a vanished elevated PID as a
cleanup race rather than replacing the original failure. Guard API revision 3
prevents an updated controller from crossing the older privileged contract.
All 110 controller, helper-contract, and plugin tests pass offline; installed
receiver retry remains the acceptance gate.

The exact-clean revision-32 build from `c716276` applied all 24 patches, passed
98 engine tests, and produced SHA-256
`f22e2fa959bfb277b12b10b8678e439438d38e7090794c7968aa4bf5f9e33e67`.
Artifact audit, candidate-only install/removal, and revision-31 to revision-32
upgrade/removal all passed. The installed package then reported 191 files with
zero alterations and guard API revision 3.

The first live window retry crossed the networkd boundary and activated a new
session-scoped P2P interface, proving the revision-32 fix. It did not reach the
portal: the session request targeted a separately advertised Samsung TV, while
the Fire TV waiting in Display Mirroring was simultaneously advertised under
its personal device label. Group formation therefore timed out after 45
seconds. Personal labels and radio addresses remain outside tracked sources.
Cleanup returned the controller to idle, kept infrastructure Wi-Fi connected,
and restored the exact pre-session networkd unit states. This is conflicting
receiver-selection evidence, not a window-capture failure. The panel no longer
lets a stationary pointer move its keyboard highlight during opening, explicit
empty-WFD peers such as the nearby printer are filtered out, and validated Fire
TV sinks sort ahead of punctuation-led generic labels. Terminal group timeouts
also take the P2P diagnostic code rather than the later DHCP code. A correctly
targeted Fire TV retry remains required. All 111 controller, helper-contract,
and plugin tests pass with these follow-up corrections.

### Privacy-safe marketplace preview refresh (2026-08-23)

The stale root preview showed a Cast desktop action despite having no selected
receiver and predated the contextual keyboard workflow. The replacement was
captured from the actual installed Omarchy panel after temporarily routing only
its read-only scan command to Omacast's deterministic demo fixture. It is a
tight 390×267 panel crop: no desktop pixels, personal receiver label, radio
address, notification, or active-window title are present. The image shows the
unselected keyboard cursor, Safe display summary, healthy readiness, and no
Cast action until a destination is explicitly selected. The installed plugin
was then restored to its exact Git payload, the shell restarted, controller
state remained idle, Hyprland config errors remained empty, and shell layout
contained exactly one Omacast widget.

### Private one-frame source preview (2026-08-23)

Quickshell 0.3.0 on the acceptance host exposes the native
[`ScreencopyView`](https://quickshell.outfoxxed.me/docs/types/Quickshell.Wayland/ScreencopyView/),
which accepts a `ShellScreen` or Wayland `Toplevel`, can omit the cursor, and
defaults to a single captured frame rather than a live feed. Omacast now keeps
the active toplevel object only in QML memory for the window prompt, or resolves
the focused output to a Quickshell screen for display casting. The preview uses
`live: false`, `paintCursor: false`, preserves source aspect ratio, and contains
no file path, subprocess, or archive operation.

The capture source exists only while the panel is open, the controller is
proved idle, and no start is pending. Closing the panel or entering any guarded
session state binds the source to null; Quickshell's implementation destroys
that capture context and clears its content buffer. The panel calls unavailable
or protected content out instead of inventing a picture, and reminds the user
that the portal remains the final window authority.

The exact installed plugin at `572b705` rendered a crisp active-window snapshot
in the real Omarchy shell with preserved aspect ratio, no cursor, fitting
captions, and no QML/screencopy warning. Escape closed the panel before the
follow-up test, and all 111 controller/plugin tests plus official manifest
validation passed. No screenshot from this private runtime test was added to
the repository. Receiver telemetry still needs to confirm the complete
preview-then-cast sequence before the acceptance checklist is closed.

The same real-shell pass exposed that `scanRunning` was absent from the bar
icon's busy predicate. The panel hero said it was looking for displays while
the bar glyph could remain idle-colored. The shared visual predicate now covers
scan plus supervised session work, and tooltips distinguish discovery,
administrator approval, receiver connection, and network restoration. An
installed read-only scan rendered both cast glyphs orange, retained the still
source preview, emitted no QML warning, and returned to the idle color without
opening the panel a second time. The controller stayed idle, Hyprland config
errors remained empty, and exactly one Omacast widget remained installed.

### Current marketplace contract preflight (2026-08-23)

Marketplace main commit `5acd4d3f4887ea17d4109636ca4ea85d9ab02e71`
keeps the existing six-heading submission body, Hardware category, and
bar/media/quickshell tags, but new listings now require a fresh exact-commit
baseline and explicit `approved-and-verified` maintainer decision. Its current
40,785-line registry contains neither `hardie.omarchy-cast` nor Omacast in
active or retired IDs.

The exact current analyzer was run locally against Omacast commit `f272e9f`.
It scanned 37 applicable runtime/readme files, found zero unsafe patterns,
returned `review-required` with `blocksApproval: false`, and reported only the
expected privilege, package-management, and service-management capabilities.
The submission draft now cites that contract commit. Because this repository
still has no Git remote, this local analysis cannot replace the mandatory
public GitHub snapshot, repository URL, owner checklist approval, or marketplace
issue authorization.

### Correct receiver portal failure and SHM fallback candidate (2026-08-23)

The next revision-32 Super+C attempt correctly targeted the waiting Fire TV and
crossed the complete guarded network path. The receiver became P2P group owner,
assigned the laptop an address, completed RTSP, negotiated 1280x720p30, and
entered PLAY. Ordinary internet simultaneously returned HTTP 200. This
supersedes the prior wrong-receiver attempt as evidence that deterministic Fire
TV selection and the contextual source request both reach the media boundary.

Window capture then failed immediately. GPU Screen Recorder negotiated the
selected 1896x1030 portal stream through PipeWire, could not import the offered
DMA-BUF modifiers, and the Hyprland portal legitimately fell back to shared-
memory frames. GSR explicitly rejects portal SHM frames, so its capture process
exited, FFmpeg received no Matroska header, and the TV stopped. The controller
had already called the session streaming because it treated an established
RTSP socket as sufficient media proof. Normal Stop still returned the
controller to idle, removed the P2P interface, user service, and inhibitor, and
restored the exact pre-test NetworkManager/systemd-networkd socket/service
states without a second authorization prompt.

Revision 33 is the replacement candidate. Patch 25 retires patch 24's GSR-
specific portal flag in the final engine surface and uses FluxCast's existing
SHM-capable PipeWire/GStreamer reader for window sources. GStreamer emits raw
I420 frames into the same FFmpeg VAAPI Safe encoder and receiver-proven paced
RTP/MPEG-TS output; display casting stays on the accepted GSR path. The portal
request asks only for one window and fails closed if returned metadata proves a
monitor, virtual output, or unknown source. The companion recipe now declares
the GStreamer and PipeWire elements that path actually executes.

A local loopback probe confirmed that the replacement portal reader accepted a
real window's SHM frames, preserved its 1896x1030 geometry, scaled it to
1280x694 with 1280x720 letterboxing, and started Intel VAAPI H.264. Later probe
attempts exposed a host picker quirk rather than a capture fallback failure:
Hyprland's portal logged the standard source-type option as unused, while the
installed preview picker defaulted to its Outputs tab and returned the full
display. The new engine rejected those selections. Omacast therefore tells the
user to open the picker's Windows tab and retains the returned source-type
check as the final privacy authority. End-to-end receiver cadence, audio, and
teardown remain pending a correctly selected window.

The controller no longer marks streaming from RTSP alone. It now requires a
completed FFmpeg progress record containing at least one encoded video frame,
uses a separate bounded post-RTSP capture-start clock, and distinguishes portal
cancellation and picker timeout from receiver negotiation and generic capture
failure. Offline patched-engine tests increased from 98 to 101 and the
controller/plugin suite from 111 to 113 before the clean revision-33 build.
The exact-clean build from commit `3e9ad8b` then passed all 101 patched-engine
tests. Its no-root artifact audit, candidate install/removal lifecycle, and
revision-32 to revision-33 upgrade/removal lifecycle passed. The candidate
SHA-256 is
`3afbf0e6dddc96cdcdcb823383ce410f6f48ac4f1bdfeaa6aec4be0dbcb5ca63`.
The audited package then upgraded the live host from revision 32 to revision 33.
`pacman -Qkk` reported all 191 package files unchanged, doctor found every
runtime dependency including `gst-launch-1.0`, and the engine advertised the
new typed portal-source capability. The git-managed plugin updated in place to
the matching controller/UI commit and passed the Omarchy validator. Receiver
acceptance remains pending.

### Picker source-type limitation (2026-08-23)

The installed picker behavior was checked against the corresponding upstream
sources rather than inferred from its UI. XDPH 1.4.1 commit `59d429b` accepts
the standard `SelectSources` options but handles `cursor_mode`, restore data,
and persistence only; it logs the portal `types` field as unused. Its custom
picker launcher passes only the global allow-token argument and does not pass
the requesting application or source type. `hyprland-preview-share-picker`
commit `7dc38ae` supports an alternate config file and a global `default_page`,
but no per-request page option. This explains why a standards-correct
window-only request can still open Omarchy's globally configured Outputs tab.

Omacast must not work around that limitation by rewriting the user's global
picker configuration, synthesizing UI input, or bypassing portal consent with
restore data. Those approaches would affect unrelated applications, introduce
races, or weaken the privacy boundary. The revision-33 candidate therefore
keeps the explicit Windows-tab instruction and treats the portal's returned
source metadata as authoritative: only source type 2 is accepted for a window
cast. A future upstream XDPH implementation that honors `types` would improve
the initial picker page.

### Screen-only product consolidation (2026-08-24)

Revision 33 proved useful failure boundaries but did not produce a simple,
reproducible window-cast experience: the portal ignored the requested source
type, the picker default could expose outputs instead of windows, and the
fallback needed a second capture stack. The product decision supersedes that
candidate rather than weakening consent or shipping two rough paths.

Revision 34 therefore returns production to the receiver-proven full-display
route. The public source selector, active-window probe, portal capability probe,
picker workflow, GStreamer dependencies, and in-panel ScreencopyView preview
are removed. Patches 24–25 remain tracked but are excluded from `patches/production/series`;
this preserves the experiment and its conflicting evidence without making it a
runtime dependency. Encoded-video-frame proof, guarded networking, indefinite
healthy sessions, Nerd Mode, keyboard-first receiver selection, and exact
cleanup remain part of the release contract.

The exact-clean revision-34 build from commit `e269955` applied the 23-patch
display series and passed all 97 patched-engine tests. The controller/plugin
suite passed 105 tests plus the official Omarchy validator. The no-root
artifact audit, fresh install/removal lifecycle, and revision-33 to revision-34
upgrade/removal lifecycle all passed. The candidate SHA-256 is
`66822a6349f1cda75edd1046e0b2afe189f7b485733e3186d163db4ad87fcb07`.
The candidate then upgraded the live host from revision 33 to revision 34.
`pacman -Qkk` reported all 191 files intact; package metadata contains none of
the removed GStreamer dependencies; FluxCast exposes the required GSR,
supplicant-trigger, and progress flags but no portal-source flag; and doctor
reports the complete screen path ready. The matching revision-34 plugin updated
in place, passed Omarchy validation, remains a single configured widget, and
the revised Super+C binding passed a Hyprland reload with no config errors.
The controller is idle, Wi-Fi is connected, P2P is disconnected, no Omacast
service or inhibitor remains, and systemd-networkd returned to its recorded
service/socket baseline. Final receiver acceptance remains pending.

### Revision 34 receiver cadence acceptance (2026-08-24)

The installed revision-34 screen-only path completed discovery, guarded P2P,
DHCP, RTSP, encoded-frame proof, and an unlimited 1280x720p30 display session
with audio. Infrastructure internet remained usable and telemetry reported no
radio retries or failures and no FFmpeg dropped or duplicated frames. The user
nevertheless found motion visibly stuttery and heard intermittent audio
glitches. During the same run, one-second encoded-frame progress periodically
fell well below 30 fps and later caught up above 30 fps, matching the earlier
long-session cadence clusters despite otherwise clean transport counters.

A controlled live A/B then changed only the internal GPU Screen Recorder
handoff from Matroska to the default-off FLV candidate from patch 23. Although
FLV had removed the large loopback packet gaps in the offline isolation, the
Fire TV result was materially worse: the user described it as not smooth and
then unwatchable. The FLV session was stopped immediately. Cleanup returned the
controller to idle, disconnected P2P, restored infrastructure Wi-Fi and the
recorded systemd-networkd baseline, and removed the session inhibitor. The
diagnostic environment override was also removed.

This is conflicting but decisive receiver evidence: the loopback improvement
does not translate into acceptable Fire TV playout. FLV is rejected as the
production default. Matroska remains the known-compatible handoff, but revision
34 has not passed smooth-motion or audio acceptance and must not be called a
supported release. The next cadence experiment must preserve the screen-only
product and isolate the Matroska capture-to-mux boundary; it must not revive
the removed portal, preview, or alternate-profile UI.

### Motion-sensitive handoff isolation and revision 35 candidate (2026-08-24)

A new offline probe reproduced the exact GSR encode, audio, handoff, final
MPEG-TS mux, and paced RTP arguments against a loopback receiver without
starting P2P or changing networking. Over roughly 45 seconds, the current
Matroska handoff produced 187 timestamp-change gaps at or above 50 ms and four
at or above 100 ms even though GSR reported 30–31 updates per second. Tightening
Matroska clusters reduced but did not remove the stalls. Omitting audio reduced
the same counts to 17 and one, respectively, which implicates the combined AAC/
video container handoff rather than raw KMS capture alone. Bounded Matroska
interleaving remained materially worse than video-only, while moving audio to
a separate FFmpeg input immediately backpressured GSR down to roughly 5–17 fps
and was rejected.

The same probe then rendered a full-screen 1920x1080 test pattern at 60 Hz to
make encoded frame sizes and desktop damage representative of moving video.
GSR still held 30–31 updates per second, but the Matroska path produced 202
timestamp-change gaps at or above 50 ms, including one above 100 ms. This
establishes that the burst problem is motion/frame-size sensitive downstream
of capture.

GPU Screen Recorder 6.0.0 accepts an MPEG-TS output handoff even though its
local manual lists only the common recording containers. A first CBR
intermediate experiment reduced stalls but emitted continuous `DTS < PCR`
warnings and was rejected. The corrected candidate keeps the intermediate TS
variable-rate with only header resend and packet flush, leaving PCR, 10.5 Mbps
CBR muxing, the receiver-proven 120% wire pacer, PID layout, and 64 ms audio
correction in the existing final FFmpeg stage. Under the same sustained-motion
load, its repeat produced zero missing RTP sequence numbers, seven gaps at or
above 50 ms, none at or above 75 or 100 ms, and a 67.2 ms maximum over 44.5
seconds. GSR remained at 30–31 updates per second. A bounded 1 MiB/0.5-second
input probe removed the otherwise five-second MPEG-TS startup analysis delay.

Patch 26 packages this as the explicit diagnostic `mpegts` GSR handoff and
keeps Matroska as the default. Revision 35 permits the private
`OMARCHY_CAST_GSR_HANDOFF=mpegts` override, rejects unknown values, and adds
engine/controller coverage. This is strong offline cadence evidence, not Fire
TV acceptance: the candidate must still pass one controlled receiver A/B,
audio presence/sync, encoded-frame startup, and exact teardown before any
default changes.

The exact-clean revision-35 builder then cloned implementation commit
`b8068a4f2b6a675f1155f82d43a284a06aaf1883`, applied the 24-patch production
series, passed all 98 engine tests, and produced SHA-256
`7748026367a791766261b34256438fd88c0f68d3a8645ffcb193558bdcd20938`.
The checksum, no-root artifact audit, fresh install/removal lifecycle, and
revision-34 to revision-35 upgrade/removal lifecycle passed. The source tree's
105 controller/plugin tests and staged Omarchy validation also pass. ShellCheck
is not installed on this host; targeted Bash and Python syntax checks passed,
while the tracked release workflow still requires ShellCheck in its builder.
Live installation and receiver acceptance remain pending.

The audited artifact then upgraded the live host from revision 34 to revision
35 through one polkit authorization. `pacman -Qkk` reported all 191 files
unchanged, FluxCast advertised the private `mkv`, `flv`, and `mpegts` handoff
choices, and doctor reported the complete screen path ready. The clean installed
plugin clone fast-forwarded to the matching controller commit and passed the
Omarchy validator. The controller remained idle; infrastructure Wi-Fi was
connected and the P2P device disconnected. The default remains Matroska.
Receiver A/B is the only next media action and requires fresh user confirmation
that the Fire TV is waiting in Display Mirroring.

### Revision 35 receiver result and 720p60 acceptance (2026-08-24)

The controlled receiver run rejected the revision-35 MPEG-TS handoff despite
its much better offline packet cadence. It negotiated and streamed with clean
transport counters, but the user still saw stutter. This supersedes the offline
candidate as a production option without erasing the useful isolation result.
Patch 26 remains a research artifact and is excluded from the release series;
Matroska remains the compatible handoff.

The next run changed one production parameter: the Safe profile moved from
1280x720p30 to 1280x720p60 while retaining Matroska, 7 Mbps, the accepted audio
path, wire pacer, and guarded network lifecycle. The Fire TV negotiated
1280x720p60. Live samples generally measured about 60.3 fps at a 1.003 realtime
ratio with zero reported dropped or duplicated frames, zero radio retries or
failures, and an empty or small RTP send queue. One later one-second sample
dipped to roughly 52.5 fps before recovery, so the telemetry is not claimed as
perfect. Subjectively, however, the user described this run as perfect. The
evidence promotes 720p60 Matroska to the single Safe release profile while
leaving repeated soak acceptance open.

The user later noticed a slight sense of playback moving behind and ahead and
suggested an optional one-second buffer. Current telemetry remained healthy at
the time. A fixed buffer would add latency but does not inherently correct clock
drift, so no speculative delay is added to the release candidate. Future work
may compare the no-extra-buffer baseline with a bounded adaptive jitter buffer.
Any user-facing option must be explicit, reversible, stream-safe, measurably
smoother across repeat runs, and honest about added latency and A/V sync.

Revision 36 converts that accepted diagnostic into the production Safe
contract and deletes the private FPS and GSR-handoff controller overrides. Its
production series excludes not only rejected FLV/MPEG-TS and portal/window
patches 23–26, but also earlier portal-only patches 7–8. The retained 20-patch
series (1–6 and 9–22) reconstructed cleanly and passed 94 engine tests. The
exact-clean builder used implementation commit
`53f0154a88351e7f6f8cce744d637c7a288f5dc8`; 104 controller/plugin tests,
staged Omarchy validation, checksum and no-root audit, fresh install/removal,
and revision-35 to revision-36 upgrade/removal all passed. The artifact SHA-256
is `6f1e99841178278822134c03774cf9300234c557edd5f11632e1f383e9103003`.

The audited artifact then upgraded the live host from revision 35 to revision
36 without stopping the active process. `pacman -Qkk` reported all 191 files
intact. FluxCast exposes the required guarded display/progress capabilities and
no private handoff or portal-source flag. The matching plugin clone updated
cleanly, passed Omarchy validation, remains the only configured widget, and
Hyprland reports no configuration error. A subsequent non-invasive 20-sample
window measured 59.44–60.51 fps and 0.991–1.003 realtime ratio; every sample
was healthy with zero dropped or duplicated frames. This does not replace a
fresh revision-36 default launch and Stop/cleanup check, because the active
process began from the earlier diagnostic launch even though its effective
media arguments match the promoted Safe profile.

That final gate then passed. A fresh normal controller launch from installed
revision 36 selected the Safe profile with no diagnostic environment override
or alternate-handoff flag. The actual FluxCast command contained 1280x720,
60 fps, 7 Mbps, VAAPI, the supported display capture backend, and session-owned
progress/network trigger paths. The Fire TV negotiated 1280x720p60. After
startup, twelve consecutive samples measured 59.22–60.48 fps and 0.987–1.004
realtime ratio with zero dropped or duplicated frames and healthy verdicts.
The user reported that video and audio looked and sounded fine.

Normal Stop returned the controller to idle, restored connected infrastructure
Wi-Fi, disconnected Wi-Fi Direct, released the user service and inhibitor,
left no media process, and removed both the session runtime directory and its
live telemetry directory. Doctor then reported the complete casting path ready.
This closes revision 36's default-launch and cleanup gate; the documented
multi-run soak and independent-machine gates still limit claims to a release
candidate rather than broad support.

Post-acceptance panel review found two release-surface defects: the live list
still presented generic WFD advertisements that had not passed the receiver
gate, and one button attempt returned to idle without creating controller state.
The release UI now filters live discovery to the validated Fire TV class,
snapshots the selected receiver for the launcher process, and shows a bounded
launcher error instead of failing silently. Nerd Mode moved from nine compact
rows to a two-column metric grid and omits unavailable deep packet/A/V cards.
The detached stream remained healthy across a real shell restart that loaded
the new QML component.

The final exact-clean artifact was rebuilt from UI/controller commit
`ce79b3e92b82ea6d7a05a8387e1a253d14eb4fb9`. All 104 controller/plugin tests,
staged Omarchy validation, 94 reconstructed engine tests, artifact audit, and
fresh package lifecycle pass. Its superseding SHA-256 is
`761726db6fa551dc36f0fa777070ade8a399e8c5c0b2373125b3a96f44fbca32`.
The panel button still requires one receiver-backed retry after the current
viewing session ends; it must not be called accepted solely from offline tests.

### Exact-purpose passwordless preparation candidate (2026-08-24)

Package revision 37 removes the recurring per-cast password prompt without
removing the privileged boundary. A declarative Polkit action grants an active
local user only the installed `/usr/lib/omarchy-cast/omarchy-cast-guard`
executable with `prepare` as argument one. `allow_any` and `allow_inactive`
remain denied. Guard API revision 4 also rejects any requested UID that differs
from Polkit's `PKEXEC_UID`; the remaining arguments retain their existing
fixed validation, ownership checks, renewable lease, and bounded cleanup.
Stop remains an unprivileged write to the user-owned session marker.

The release surface was simplified at the same time. Clicking a displayed TV
starts the same action as Enter, the redundant pre-cast button is gone, Enter
on the live view requests Stop, and the documented/live Super+C binding uses
Omarchy's native `shell toggle` route so a second press closes the panel. The
FluxCast base pin was expanded from its historical abbreviated identifier to
the full `9d27c39670940ada3a0e520a1d70574910646083` commit for publication.

All 105 controller, package-contract, and QML surface tests pass. The Polkit
action is parsed and compared structurally in tests, and artifact/lifecycle
audits require its exact mode and guard API contract. The disposable revision
36-to-37 upgrade/removal lifecycle passes. Its first audit also exposed that
the historical final `pacman -Qkp` command checked archive members against the
live root; it is superseded by the no-root package-metadata query because paths,
modes, XML, and executables are already checked against the extracted archive.
The final exact-clean artifact was then rebuilt from commit
`66865bc6d05b04174024e0d36043bd8f2cef2811`. Its 94 reconstructed engine tests,
no-root artifact audit, checksum verification, fresh install/removal, and
revision-36-to-37 upgrade/removal lifecycle all pass. The artifact SHA-256 is
`c3e9282e473cc4c10f60e64b95a4b1d21007f9cc478b169a143d02b0ec4739c7`.

Revision 37 upgraded the live host with 195 package files and zero integrity
changes. The installed action reports `allow_any=no`, `allow_inactive=no`, and
`allow_active=yes`, with exact path and argument annotations. `pkcheck` granted
the active session, and a deliberately malformed `prepare` invocation reached
the helper's usage rejection without prompting or touching networking. Doctor
reports API revision 4 and the complete path ready. The live plugin clone
fast-forwarded to the same code and a shell restart plus two toggle invocations
produced no Omacast/QML error; Hyprland reports no configuration error.

Receiver-backed click/Enter start, keyboard Q Stop, and automatic TV-side
disconnect remain the final hardware acceptance cycle. No untested receiver
result is inferred from the policy and shell-level checks.

The first revision-37 panel cast started without a recurring password prompt
and reached healthy 1280x720p60 streaming. Stopping Display Mirroring on the TV
then exposed a liveness defect: the P2P interface and capture/mux descendants
were gone and media progress had frozen, but the FluxCast parent retained an
ESTABLISHED RTSP socket with queued bytes. The supervisor therefore continued
to publish `streaming`. Manual Stop restored idle, left the user service
inactive, removed the P2P group, and preserved infrastructure Wi-Fi.

The candidate fix requires the session-named P2P group interface to remain
present after streaming begins. Three seconds of continuous absence returns a
normal `receiver disconnected` completion through the existing ownership-safe
cleanup path. This preserves the contradictory socket evidence instead of
treating RTSP ESTABLISHED as proof of an active receiver. Offline helper and
contract tests pass; one receiver-side Stop retry remains required.

The next panel session again started passwordlessly through click-to-cast and
reached healthy 1280x720p60. Keyboard review found that N and Q were initially
gated on the final streaming phase even though the controls were visible while
connecting; R worked at idle and isolated the issue to phase gating rather than
focus delivery. The live UI now accepts N throughout an active session and Q
throughout every busy connection/streaming state. Visible shortcuts use muted
suffixes—`Rescan (R)`, `Show Nerd Mode (N)`, and `Stop casting (Q)`—rather than
leading letters. The current cast survived the shell-only reload. N/Q and the
new receiver-disconnect watchdog still require final direct user acceptance.

The open receiver-disconnect conclusion is now superseded. Session
`42ec865736f54179a19c50e571d13fbf` started passwordlessly from the panel,
negotiated 1280x720p60, and reported healthy cadence, zero dropped/duplicated
frames, no radio failures, and an empty send queue. After Display Mirroring was
closed on the receiver, the supervisor recorded a `completed` transport result
with detail `receiver disconnected`, then transitioned through `stopping` to
`idle`. The service, inhibitor, FluxCast/capture/mux processes, and session P2P
interface were absent afterward; infrastructure Wi-Fi was connected and the
pre-existing systemd-networkd service state was restored. This is direct
receiver evidence for the three-second liveness watchdog rather than an
inference from offline tests.

The publication audit refreshed the marketplace contract to exact commit
`55f3491b665e72e72ad12ec8718ee49609db09b6` and ran that snapshot's V3 static
scanner logic over the tracked local tree with its real inclusion/exclusion
rules. The outcome is `review-required` with zero findings. Its only
capabilities are the expected `privilege`, `package-manager`, and
`service-management` sets; the full FluxCast source pin avoids an unpinned
remote-execution finding, and the purpose-built Polkit action produces no
dangerous sudoers finding. This result is predictive rather than marketplace
evidence because the repository does not yet have a permanent public GitHub
URL; submission must rescan the exact public commit.

### Public-release presentation acceptance (2026-08-24)

After the public repository was created, the exact marketplace validator and
baseline were run against commit `04e7f8f05869743d47887e2da8260c387000b1af`
through GitHub rather than a local snapshot. Quattro validation passed with the
root preview detected. The baseline remained `review-required`, with zero
findings and only the expected privilege, package-management, and
service-management capabilities.

A first screenshot session reached the controller's explicit
`p2p-negotiation-failed` timeout and was recovered to idle. A fresh discovery
and retry then reached genuinely healthy 1280x720p60 streaming: approximately
59.7 measured fps, zero dropped or duplicated frames, a 1.003 realtime ratio,
and healthy radio and pipeline verdicts. Nerd Mode was opened in the real panel
and captured as a panel-only public image. The crop contains no receiver name,
radio address, network name, or desktop content.

Normal Stop returned the controller to idle, released the service and
inhibitor, and restored infrastructure Wi-Fi. The disconnected P2P client
interface remained visible in a down state for at least five seconds after
idle. A later exact-session guard cleanup removed it, but an inadvertent
diagnostic `prepare` call occurred between those observations, so this run does
not prove whether ordinary delayed supplicant teardown alone would have removed
the interface. Preserve this ambiguity and repeat the bounded post-Stop
observation before making a stronger immediate-cleanup claim.

### Marketplace runtime-boundary review (2026-08-25)

The marketplace maintainer reviewed exact public commit `965f94d` and found no
new problem in the fixed privileged command surface, caller/UID binding, or
ownership checks. The blocking findings were instead at the unprivileged data
boundaries: complete subprocess and QML collector output, uncapped shell model
arrays/strings, automatic rich-text interpretation of receiver/controller
labels, and an unrestricted pre-validation read of `state.json`.

The focused remediation does not change FluxCast, guarded networking, package
installation, media settings, or session ownership. Discovery now drains both
subprocess pipes while retaining at most 65,536 bytes from each and discards
FluxCast's human scan diagnostics directly to `/dev/null`. Receiver iteration,
host models, messages, and controller responses have explicit limits. The
controller emits at most 262,144 bytes to QML. The panel replaces Quickshell's
complete-output `StdioCollector` with an empty-marker streaming `SplitParser`,
retains no more than 262,144 characters across its chunks, checks the received
string before parsing, projects responses into small allowlisted models, and
uses `Text.PlainText` for every radio- or controller-derived label. The former
unbounded stderr collector was replaced with a streaming zero-retention discard
parser so Qt also cannot accumulate an unread stderr channel.

Runtime state is opened with no-follow semantics, checked as a private regular
file owned by the session user, limited to 65,536 bytes, and JSON-budgeted
before schema validation. Current telemetry receives the same descriptor-based
checks under its existing 262,144-byte ceiling, closing the earlier stat/read
race. Offline adversarial tests cover oversized stdout and stderr without
newlines, timeouts, excessive/deep state, symlinks, receiver floods, discarded
scan diagnostics, control characters, and markup-like receiver names. No live
receiver or network test is required because the accepted streaming and guard
paths are unchanged.

A follow-up review at exact public commit `540f578` identified that a FIFO
could block the initial read-only `open()` before those descriptor checks ran.
An isolated reproduction replaced `state.json` with a private FIFO and caused
`omacast status` to wait until an external deadline. The shared state/current-
telemetry reader now adds `O_NONBLOCK` before opening, then retains the existing
same-descriptor regular-file, owner, mode, and size checks. A subprocess test
with its own two-second ceiling verifies that a FIFO reaches the regular-file
rejection rather than hanging the suite. This changes no networking, media, or
privileged-helper behavior; the separate follow-up QoS finding remains open.

The root marketplace preview was also changed from the compact idle panel to a
16:9 presentation of the genuine receiver-backed Nerd Mode capture. Marketplace
desktop cards use a fixed landscape area with `object-fit: cover`, so the prior
portrait image would have lost most of its telemetry. The new composition keeps
the original panel pixels and privacy redactions intact over a non-identifying
violet/cyan desktop backdrop; `nerd-mode.png` remains the source capture.

### Post-review live smoke (2026-08-25)

The installed plugin was moved from its clean pre-sanitization history onto
exact pushed remediation commit `4ec0f62`, passed Omarchy validation and a real
shell restart, and produced no plugin/QML error in the shell log. With the user
confirming that the receiver was waiting and accepting the temporary network
impact, the production controller discovered one validated Fire TV and started
the normal guarded `safe` mirror path. It reached `streaming` after encoded-frame
proof and negotiated `1280x720p60`.

After warm-up, twelve consecutive one-second observations reported `healthy`,
59.49–60.50 measured fps, 0.992–1.008 realtime ratio, zero FFmpeg drops, zero
duplicates, and no health issues. The UI toggle was exercised against the real
shell and its log remained free of Omacast/QML errors. This was a short plumbing
smoke test; the user did not provide a new subjective motion or audio verdict,
so it does not supersede the earlier receiver acceptance evidence.

Cooperative Stop returned the controller to `idle`, marked transport cleanup
complete, removed the current session's live telemetry/runtime directory,
released the transient service and all Omacast/FluxCast media processes, and
left the managed Wi-Fi station connected with its default route. A kernel P2P
client interface remained visible for at least 20 seconds, but it was down,
disconnected, carried no IPv4 address, and had no owning cast process. This is
consistent with the previously recorded delayed supplicant teardown ambiguity;
the guard still removes such a proven-down, unaddressed stale interface before
the next session. Do not reinterpret this smoke test as proof of immediate
kernel-interface disappearance.

### Follow-up privileged QoS review (2026-08-25)

Marketplace review of exact public commit `540f578` showed that the root guard
read a user-owned `qos.pid` and identified the requested process using owner,
command-line, short-name, and descendant checks. An isolated reproduction
created a same-UID process with an allowlisted `ffmpeg` short name and a command
line containing `fluxcast`; it satisfied those identity predicates without
belonging to Omacast. The action could therefore grant negative nice to an
arbitrary same-user process even though it could not target another UID or run
an arbitrary root command.

The remediation removes the PID file and every privileged scheduling action
rather than adding more spoofable process labels. Guard API 5 and companion
revision 38 contain no `qos.pid`, `/proc` media traversal, or `renice`. The
already supervised `omacast-session.service` instead requests
`CPUWeight=10000`, which applies only within the user-owned service cgroup and
requires no privilege. Offline systemd probing confirmed that the user manager
accepts this property and has the CPU controller. Because cgroup weight is not
identical to negative nice, receiver cadence remains a final acceptance test
before publishing version 0.1.1.

### Revision 38 receiver acceptance (2026-08-25)

The audited revision-38 companion upgraded the live host from revision 37 with
all 195 package files intact and guard API 5 available at the immutable helper
path. The installed plugin was then fast-forwarded to the matching 0.1.1
controller, passed Omarchy validation, and reported the complete casting path
ready. The initial compatibility warning was valid: the previously installed
0.1.0 controller still expected guard API 4 and was not used for acceptance.

A normal panel launch negotiated 1280x720p60 and entered healthy streaming. The
live transient user service reported `CPUWeight=10000`; the current telemetry
directory contained no `qos.pid`. Consecutive samples held approximately 60
fps and realtime speed with zero dropped or duplicated frames, an empty send
queue, zero transport drops/errors, and no radio retry or failure deltas. The
user accepted both picture and audio quality.

Keyboard Stop returned the controller to idle. The transient service and media
processes exited, the current session's user and root runtime paths were gone,
NetworkManager was active, and the managed Wi-Fi station had reconnected. This
closes the cadence and lifecycle acceptance condition for replacing privileged
negative nice with user-owned cgroup weighting; it does not close the separate
cold-boot or forced-failure matrix.

### Pre-push privileged lease audit (2026-08-25)

Reviewing adjacent variants of the FIFO finding exposed a check/open race in
the privileged lease path. The guard and recovery process first classified the
predictable user-owned heartbeat as a private regular file, then reopened its
path with a normal shell read. An induced same-UID replacement after the check
but before the read changed the path to a FIFO and held the reader until an
external timeout.

Revision 39 opens the heartbeat once, verifies the type, owner, mode, and size
on that exact descriptor, and pins the inode for the session. Each renewal read
reopens only `/proc/self/fd/<verified-fd>`, consumes at most 32 characters, and
has a short read timeout. The independent recovery process uses the same
contract. Regression probes prove that a direct FIFO is rejected without
blocking and that replacing the pathname after verification cannot redirect
the pinned lease read. Exact-package and receiver lifecycle acceptance remain
open for this new package payload.

### Pre-push marker-directory audit (2026-08-25)

The final review of user-influenced privileged paths found that the guard
created its user-owned marker directory beneath `/run/user/$UID`. Although it
checked that the destination did not exist first, GNU `install -d` follows a
directory symlink at its destination. A harmless temporary reproduction showed
that `install -d -m700` changed the symlink target's mode. A same-UID process
could therefore race the absence check and redirect the root helper's later
`-o` and `-m` changes.

Revision 40 / guard API 6 moves the writable marker directory to `user/`
beneath the root-owned session directory. The parent is created root:root 0711,
the child is created for the authenticated UID at 0700, and both are validated
after creation. The user can write trigger, heartbeat, and Stop markers but
cannot replace the child directory entry because the parent is not writable.
The unused privileged `stop` verb and controller builder were removed; normal
Stop continues to create the session marker without a second authorization.
Exact-package and receiver lifecycle acceptance remain open.

### Revision 40 receiver cleanup finding (2026-08-25)

The revision-40 receiver test negotiated 1280x720p60 and remained healthy at
approximately 60 fps with zero FFmpeg drops or duplicates, no radio failures,
and an empty send queue. The new root/user runtime boundary had the intended
0711/0700/0600 ownership and modes. UI Stop returned the controller to idle,
removed the service, media processes, privileged runtime, policy, and network
files, and restored infrastructure Wi-Fi.

The strict post-stop check nevertheless found the session-created
`p2p-wlp58s0-2` P2P-client netdev down but still present with link-local IPv6.
FluxCast had issued its supplicant Disconnect and the journal recorded nl80211
deinitialization, but the kernel device remained until it was explicitly
removed. This supersedes the initial `cleanup_complete` result for revision 40.

Revision 41 / guard API 7 creates a root-owned `p2p-interfaces` record after
proving the pre-session P2P baseline clean. Normal and independent recovery
cleanup enumerate the dynamically resolved `p2p-$interface-*` devices once,
before NetworkManager resumes, then delete only recorded entries that still
identify as P2P-client devices. Unrelated or unrecorded interfaces are ignored,
and no interface polling runs during media delivery. Exact package and receiver
cleanup acceptance remain open.

The same audit removed a five-times-per-second `iw dev` poll from the first
revision-41 draft; the clean baseline makes one teardown enumeration sufficient.
It also found that the controller ignored helper output after readiness and
therefore could still label an incomplete privileged cleanup successful. The
controller now reads at most 64 KiB after the helper exits and requires the last
status to be schema-valid, session-matched, successful, and explicitly
`cleaned`; otherwise it raises `guard-cleanup-incomplete`.

### Revision 41 receiver cleanup acceptance (2026-08-25)

The exact installed revision-41 plugin and companion package started the normal
unbounded Safe cast through the same executable entry point used by the panel.
Session `4d2b54f7e0e4451d8cdac3bc98026693` negotiated 1280x720p60 and, after
warm-up, reported 59.5 measured fps, a 0.992 realtime ratio, zero FFmpeg drops
or duplicates, zero radio failures, and an empty send queue. The live user
service held `CPUWeight=10000`. The user accepted picture and audio quality.

During the session, the privileged boundary had the intended root-owned 0711
session parent, user-owned 0700 marker directory and 0600 heartbeat, plus
root-owned 0600 `p2p-armed` and `p2p-interfaces` records. Normal UI Stop returned
the controller to idle and the collectable service disappeared successfully.
The session-created `p2p-wlp58s0-3` P2P-client netdev was absent from both
`ip link` and `iw dev`; only the normal managed Wi-Fi interface and non-netdev
P2P device remained. Infrastructure Wi-Fi was connected, NetworkManager,
systemd-networkd, and wpa_supplicant matched their active pre-test state, no
FluxCast, capture, mux, guard, or recovery process remained, and no session
runtime child or temporary network/DBus file remained. The persistent empty
root-owned `/run/omarchy-cast` container is package runtime infrastructure, not
session residue.

This closes revision 41's normal receiver-backed cleanup acceptance. Forced
helper death, cold-boot behavior, other adapters/receivers, and longer soak
coverage remain separate open acceptance work.

### Marketplace session-lock follow-up (2026-08-26)

Review of exact commit `74e9937f2490c496f6a9635cdb0696b504ccf581`
found that `SessionLock.acquire()` opened predictable `session.lock` with
path-following `open("a+")` and then applied `chmod` to the pathname. A local
reproduction pre-placed a symlink and confirmed that acquisition changed an
unrelated user file from 0644 to 0600 while locking the wrong inode.

The focused remediation opens the private runtime directory and lock relative
to validated descriptors. Both path components use no-follow semantics; the
lock also uses nonblocking mode so a FIFO cannot stall before classification.
The exact lock descriptor must identify a regular, current-user-owned inode
with one link before permissions are applied through `fchmod` and `flock` is
taken on that same descriptor. The adjacent `session_lock_is_held()` probe uses
the same anchored validation without creating or repairing an unsafe path.

Regression tests reject lock symlinks, a symlinked runtime directory, FIFOs,
and hard links without blocking or changing their targets. Existing
single-owner, Stop, recovery, dry-run, simulation, and fake-transport behavior
continues to pass. This changes no privileged helper, networking, media, or
receiver path and therefore does not require a new Fire TV acceptance run.

### Selected-receiver RTSP admission (2026-08-26)

The exhaustive review found that FluxCast's RTSP listener accepted any client
that could reach its port, used that client's source address as the media
destination, and allowed multiple handlers to start capture. Omacast's
`--wfd-no-firewall` launch contract made engine-level receiver authentication
the required safety boundary rather than an optional firewall optimization.

Production patch 27 passes the selected discovery MAC and base Wi-Fi interface
into the RTSP server. Before either passive negotiation or the active probe can
claim a session, the peer address must resolve through the kernel neighbour
table on the exact session-created `p2p-<interface>-*` link. An exact selected
MAC is accepted. Because a receiver may use a distinct group-interface MAC,
the sole neighbour on a clean, session-owned P2P interface is also accepted;
multiple nonmatching neighbours fail closed. The active probe no longer falls
back to a conventional unverified address.

Admission is atomic and permits one authenticated receiver. Rejected sockets
cannot set the connected state or enter RTSP negotiation, and ownership is
released after normal or exceptional handler exit. Nine focused identity and
handler-boundary tests pass. The exact 21-patch reconstruction passes all 103
engine tests; the repository passes all 131 controller/plugin tests and its
staged installable payload validates. Direct root validation still sees an
ignored research symlink under `work/`, which is not part of that payload. Live
receiver acceptance remains open; no network operation was needed here.

### Root recovery telemetry boundary (2026-08-26)

The exhaustive review found that the independent root recovery helper removed
fixed telemetry filenames beneath `/run/user/<uid>/omarchy-cast/telemetry/`.
Although the filenames were allowlisted, their parent hierarchy was controlled
by the user. A replaced session directory could therefore redirect root's file
operations outside the cast telemetry directory.

Revision 43 removes that traversal instead of attempting to make root manage
user-owned data. Normal transport teardown and explicit stale-state recovery
already call the controller's `cleanup_live_telemetry()` as the session user.
After an abrupt controller death, bounded volatile telemetry may remain until
the user chooses Recover or the user runtime directory disappears at logout;
privileged network, firewall, policy, P2P-interface, and root-owned runtime
cleanup remain unchanged.

A dedicated package regression requires the root recovery source to contain no
`/run/user/`, telemetry path, or live telemetry filename, and the built-package
auditor enforces the same rule. All 132 controller/plugin tests and staged
payload validation pass. This narrows only the cleanup privilege boundary and
needs no live receiver run.

### Session event and Stop-file boundary (2026-08-26)

The exhaustive review found that event appends and Stop writes followed
predictable paths before validating their inodes. Event/history readers used
separate path checks and unbounded reads, while active state accepted arbitrary
nonempty session IDs that stale recovery later reused as event-log paths.

The controller now requires 32-character lowercase hexadecimal session IDs in
active state and event APIs. Event append, retention, history, and explicit
reads operate through a validated private sessions-directory descriptor.
Appends validate a current-user-owned, single-link regular file before writing;
reads add no-follow/nonblocking opens, same-descriptor validation, a 1 MiB
ceiling, and bounded JSON shape. History considers only safe filenames and
inodes.

Stop requests use unpredictable exclusive temporary names inside the validated
runtime descriptor, then replace and fsync descriptor-relatively. The
supervisor reads at most 4 KiB from a private, single-link regular file and
rejects links, FIFOs, malformed JSON, unexpected shapes, and other-session
requests. Cleanup unlinks only relative to the same validated directory.

Adversarial tests preserve symlink and hard-link targets, reject FIFOs without
blocking, reject oversized inputs and invalid state IDs, and retain normal
simulation, history, Stop, stale recovery, and transport behavior. This changes
no privileged helper, network, capture, or receiver path. All 139 offline tests,
Python compilation, and staged payload validation pass.

### State and telemetry directory anchoring (2026-08-26)

The exhaustive review found that hardened final-file reads were still reached
through replaceable parent pathnames. State writes recreated
`$XDG_RUNTIME_DIR/omarchy-cast` as a path before temporary creation and atomic
replacement. Telemetry creation, snapshot replacement, cleanup, archive
append/retention, and engine progress/log handling had the same weakness. The
auxiliary telemetry reader also performed `stat()` before a path-following
`open()`, allowing both replacement races and FIFO blocking.

State reads and writes now retain the validated runtime directory descriptor.
Writes create an unpredictable exclusive private temporary, validate and sync
that inode, then replace and sync descriptor-relatively. Live and archived
telemetry directories are opened component-by-component with no-follow
semantics, current-user ownership, and private modes. Snapshot replacement,
archive append, retention, and cleanup operate only relative to those pinned
directories; cleanup verifies that the live session name still identifies the
pinned directory before removing it.

The media boundary required an additional measure because FluxCast and FFmpeg
accept path arguments. The controller now preopens and validates progress,
latency, packet, and engine-log files, keeps their descriptors for the session,
and passes `/proc/<controller-pid>/fd/<descriptor>` paths to the engine. This
lets FluxCast and its FFmpeg child reopen the exact inode without depending on
the replaceable telemetry filename. Sampling reads the same pinned descriptors.

Offline regressions cover symlinked directories, final-file symlinks and hard
links, FIFOs, induced parent replacement after open, archive redirection, and a
two-generation subprocess write through the descriptor-backed engine path.
Unrelated targets remain unchanged. The change affects only unprivileged local
state/telemetry handling and does not alter P2P, privileged cleanup, capture,
encoding, or receiver negotiation, so no Fire TV run is required.

### Renewable heartbeat writer anchoring (2026-08-26)

The privileged guard and independent recovery process already opened the
user-owned heartbeat once, validated its inode, and retained that descriptor.
The unprivileged writer did not match that contract: every renewal reopened the
predictable pathname with `O_TRUNC` before checking what it referenced. A FIFO
could block the renewal thread during `open()`, while a hard link to another
user-owned file was truncated before any later validation could reject it.

`SessionLease` now opens the private marker directory with directory/no-follow
semantics and verifies its current-user ownership and `0700` mode. It opens or
exclusively creates `heartbeat` with no-follow and nonblocking flags but without
truncation, then validates that exact descriptor as a private, single-link,
current-user-owned regular file no larger than 32 bytes. Only then does the
first renewal truncate and write. The controller keeps both directory and file
descriptors until Stop; every later renewal revalidates and updates the pinned
file descriptor with positional writes rather than reopening the pathname.

This intentionally does not use atomic replacement. Both privileged readers
pin the original heartbeat inode after guarded setup, so replacing it on every
renewal would leave them observing stale data. An induced post-open pathname
replacement instead proves that the controller and a guard-like reader keep
observing the original inode while the replacement target remains unchanged.
Additional offline regressions reject symlinks, hard links, FIFOs, oversized or
public files, and unsafe parent directories before modification. The change is
limited to the unprivileged lease writer and requires no receiver or network
test.

### Receiver-facing RTSP resource bounds (2026-08-26)

The exhaustive review found that FluxCast read receiver RTSP input without an
aggregate header count or byte ceiling, accepted arbitrary `Content-Length`
values, and used an unbounded thread-per-connection server. The passive path
also had no read deadline, so an authenticated but stalled or defective sink
could retain its worker indefinitely before streaming.

Production patch 28 limits a start/header line to 8 KiB, a message to 64
headers and 64 KiB of aggregate headers, and a body to 64 KiB. Content length
must be one non-negative ASCII decimal value within that body ceiling, and the
reader requires the complete declared body. Invalid, duplicate, oversized,
truncated, and incomplete messages close the session rather than being treated
as a partial negotiation.

The passive listener now permits four concurrent workers. Negotiation and any
message already in progress must complete within ten seconds. After media has
started, the receiver may remain silent indefinitely; once its first next byte
arrives, the same completion deadline applies. This distinction preserves
long-lived receivers that do not send periodic requests while preventing a
partial message from pinning the selected session. The already bounded active
probe now also handles parser rejection as a normal I/O failure.

Fourteen focused regressions cover normal and maximum accepted messages,
per-line/header-count/aggregate/body ceilings, malformed and duplicate lengths,
truncated input, negotiation and established-message stalls, excess workers,
and worker-start failure. The exact 22-patch reconstruction passes all 117
engine tests. A short receiver connect/stream/Stop acceptance remains open
because this changes the RTSP timing path; no live network operation was run
during the offline remediation.

The clean-built revision-44 package was subsequently installed over revision
41 and exercised through the normal Omacast GUI with the stock Fire TV waiting
in Display Mirroring. Negotiation completed, the desktop and audio played, and
the user reported that the result looked good. The user then stopped through
the GUI. The controller returned to `idle`; FluxCast, media, guard, RTSP socket,
and transient session service were absent; the session P2P client was removed;
and the original infrastructure Wi-Fi remained connected. This closes the
receiver acceptance gate for patch 28 without claiming a soak or broader
receiver result.

### Unlimited-session diagnostic quotas (2026-08-26)

The exhaustive review found that the supported until-stopped cast could append
diagnostics for its complete lifetime. Bounded readers protected JSON parsing
and UI memory but did not cap the files themselves. Local archives measured
about 1.5 KiB per one-second sample, enough for roughly 130 MiB per day from the
persistent archive alone. The FFmpeg progress file updated four times per
second, FluxCast stdout appended for the session, and the hidden packet-trace
environment override could enable a much higher-rate framecrc output.

The controller now stops the per-session archive at 8 MiB and marks the live
snapshot `historyCapped` while continuing one-second live Nerd Mode updates.
FluxCast and descendant output is continuously drained through a pipe into a
256 KiB recent-tail collector bound to the preopened engine-log inode. The
collector reads fixed 8 KiB chunks, so even a producer without newlines cannot
create an unbounded in-memory line. The supported production command no longer
honors `OMARCHY_CAST_PACKET_TELEMETRY`; packet tracing remains preserved only as
engine/research capability rather than a latent product output.

Production patch 29 changes only the supported GSR/wlroots progress path.
FFmpeg writes progress to its stderr pipe; a FluxCast drain thread separates
bounded progress records from diagnostics and replaces the preopened progress
inode with only the latest complete record, capped at 16 KiB. Each replacement
revalidates a private, current-user-owned regular descriptor before truncation.
This avoids truncating behind an independent append writer, which could have
created sparse files while leaving the writer's offset unbounded.

Controller regressions fill the complete archive quota without exceeding it,
prove live snapshot writes continue, flood the engine collector beyond its
limit, and prove the removed environment override cannot restore packet output.
Engine regressions feed ten thousand progress records, oversized partial lines,
and unsafe link/public targets. The exact 23-patch engine passes 120 tests and
the repository passes 156 tests. A real local FFmpeg run retained only its
180-byte final record after 60 frames. Revision 45 then built from a clean clone
of the finding commit, passed the no-root artifact audit, and completed a
disposable install/removal lifecycle. Receiver acceptance remains open because
the supported media subprocess stderr/progress plumbing changed; no live
network operation was run for this remediation.

The clean revision-45 package and matching plugin commit were then installed on
the live host and exercised through the normal GUI against the stock Fire TV.
The session negotiated 1280x720p60 and entered `streaming`; Nerd Mode received
the bounded latest-record progress data. After startup it measured roughly
60–62 fps with realtime ratio near 1.0, zero FFmpeg drops or duplicates, no
radio transmission failures, and no transport errors. The user completed the
requested test and Stop was observed cooperatively. The controller returned to
`idle`, the session P2P client and media processes were absent, and the original
infrastructure Wi-Fi remained connected. This closes patch 29's short receiver
gate without claiming a soak test.

### NetworkManager process identity (2026-08-26)

The exhaustive review found that the privileged helper resolved and validated
NetworkManager's numeric PID only once, before a trigger wait of up to five
minutes, then retained it through activation, the complete cast, cleanup, and
the independent recovery command line. Linux can recycle a PID after its
process exits, so either later numeric `SIGSTOP` or `SIGCONT` could target an
unrelated process despite the original `/proc/<pid>/comm` check.

Guard API revision 8 removes the retained PID and recovery argument. The main
helper now asks systemd to signal only the current main process belonging to
`NetworkManager.service`. Before the pause request it creates a private
root-owned `network-manager-resume-required` marker in the protected session
directory; a failed pause removes that marker. Normal cleanup and independent
recovery validate the marker before issuing a unit-scoped `SIGCONT`, retry the
resume three times, remove the marker only after success, and otherwise retain
the root token and marker as explicit incomplete-cleanup evidence. Calling
resume again after success is a no-op.

Regression harnesses prove unit-scoped STOP/CONT arguments, pause failure,
three bounded resume retries, preserved ownership after resume failure,
idempotent repeated cleanup, and the absence of `nm_pid` or numeric STOP/CONT
signals from both production helpers. The controller requires API revision 8
and package revision 46 carries the matching immutable helpers. The full 158
test repository suite, Python compilation, plugin validation, Bash syntax
checks, and simulation pass. After installing the repository ShellCheck 0.11
package, the complete production shell-lint gate also passes. Revision 46 then
built from a clean clone, passed all 120 FluxCast tests and the no-root artifact
audit, and completed a disposable revision-45 to revision-46 upgrade/removal
lifecycle. A live receiver run remains open because privileged pause/resume
signalling changed.

The final revision-46 package and matching API-8 plugin were then installed on
the live host and exercised through the normal GUI against the stock Fire TV.
The session negotiated 1280x720p60 and entered `streaming`. Its final sample
measured 60.34 fps with realtime ratio 1.01, zero FFmpeg drops or duplicates,
zero radio failures or retries, no transport errors, and healthy status. The
user reported that it worked and stopped the session cooperatively. The
controller returned to `idle`; NetworkManager was active and running; the P2P
client and cast/media processes were absent; and infrastructure Wi-Fi remained
connected. This closes finding 8's receiver gate without claiming a soak test
or exercising forced independent recovery on the live network.

### Failure-tolerant privileged cleanup (2026-08-26)

The exhaustive review found that both root helpers used fail-fast shell mode
around multi-path removal and recovery operations. The authenticated local user
owns the session marker directory and could place a directory at `trigger`,
`heartbeat`, or `stop`. GNU `rm -f` rejects directories, so any such leaf could
abort cleanup before the D-Bus reload, systemd-networkd restoration, ownership
record handling, and final status. Independent recovery also exited immediately
when NetworkManager resume failed, suppressing every later restoration attempt.

Both helpers now remove each expected leaf independently, aggregate failures,
and always proceed through the remaining safe restoration stages. D-Bus reload,
NetworkManager resume, firewall removal, systemd-networkd restoration, recorded
P2P-client cleanup, and fixed ownership-record removal no longer depend on an
earlier unrelated command succeeding. Unexpected marker directories are not
removed recursively. If any required step remains incomplete, the root token
and the relevant restoration records remain as evidence and the normal helper
reports incomplete cleanup rather than a false success.

Deadline-bounded harnesses exercise directories at all three user-controlled
marker names against both cleanup owners. They also inject independent
NetworkManager-resume and networkd-restore failures and prove that later D-Bus
and state restoration is still attempted. Package revision 47 carries the
updated API-8 helpers; no helper command or authorization surface changed. All
160 repository tests, staged Omarchy validation, Bash syntax, production
ShellCheck, and git whitespace checks pass. The exact-clean revision-47 build
passes all 120 FluxCast tests, the no-root artifact audit, and disposable
candidate installation/removal. This is offline failure-injection evidence;
forced privileged recovery on the live host remains part of the final
acceptance matrix.

### Pre-mutation recovery ownership (2026-08-26)

The exhaustive review found that the primary root helper created the temporary
networkd configuration and D-Bus policy, reloaded D-Bus, and only then launched
independent recovery. `setsid ... &` was not checked. `SIGKILL`, a helper crash,
or an immediately failing recovery executable could therefore leave a short
interval in which privileged state existed without a verified cleanup owner.

Package revision 48 / guard API revision 9 separates protected session identity
from external privileged mutation. The primary helper first creates only the
root-owned session parent, user marker directory, random root token, and empty
P2P ownership record. It then launches the fixed recovery executable and waits
up to five seconds. Recovery independently validates the protected parent,
user-directory ownership, token type/mode/value, and then publishes a root-owned
0600 readiness marker. The primary helper also verifies that marker and the
child's liveness; failure blocks setup before networkd or D-Bus files exist.

Only after acknowledgement does the helper prepare networkd and D-Bus state.
The networkd service-state snapshot is written to a root-owned pending file and
atomically renamed before later activation can begin. Both cleanup owners use
presence-driven restoration, so interruption with only identity, a pending
snapshot, a committed snapshot, or a policy file is handled without assuming a
later initialization stage completed. Successful cleanup consumes the recovery
marker with the token; incomplete cleanup retains both as ownership evidence.

Deadline-bounded regressions prove successful acknowledgement, failed child
startup, rejection of an unsafe token, ordering before privileged mutation,
and all four partial initialization states. The controller explicitly rejects
API revision 8. All 163 repository tests, staged Omarchy validation, Bash
syntax, production ShellCheck, Python compilation, and git whitespace checks
pass. The exact-clean revision-48 build passes all 120 FluxCast tests, the
artifact audit, candidate installation/removal, and disposable revision-47 to
revision-48 upgrade/removal. No live package or network state was changed.

### Transactional media startup cleanup (2026-08-26)

The exhaustive review found that the supported GPU Screen Recorder path did
not publish either child into the pipeline's owned process list until both
capture and FFmpeg had launched and survived their readiness check. If FFmpeg
failed to spawn, capture remained outside normal teardown. The two
immediate-exit branches also sent a termination signal to the sibling without
waiting for it, so a failed cast could leave a running or unreaped child.

Production patch 30 now keeps every successfully spawned child in a private
startup transaction. Any exception, including interruption during the
readiness delay, closes the parent copy of the capture pipe and reaps children
in reverse launch order with bounded terminate, wait, kill, and final-wait
steps. The original startup exception is preserved. Only a fully healthy pair
is published to the normal pipeline owner; the progress-drain thread follows
the same success boundary.

Failure-injection regressions cover FFmpeg spawn failure, immediate capture
exit, immediate FFmpeg exit, a child that ignores termination, and successful
ownership publication. The exact 24-patch reconstruction passes all 125
FluxCast tests. Package revision 49 carries the new engine patch without a
guard API change. All 163 repository tests, staged Omarchy validation,
production ShellCheck, and git whitespace checks pass. This remediation does
not change networking, privilege, receiver negotiation, or the successful
media command line, so no live Fire TV test was run.

### Validated RTSP progress ownership (2026-08-26)

The initial exhaustive-audit wording said that any TCP connection could
suppress the one-shot active RTSP fallback. Selected-receiver authentication
in production patch 27 had already narrowed that prerequisite: an unrelated
host cannot claim the server. The remaining availability bug was that a socket
attributable to the selected receiver became `has_connected_client` before it
produced a valid RTSP message. A silent or malformed passive connection could
therefore cancel the four-second active fallback and later fail itself.

Production patch 31 separates verified identity, unconfirmed socket
reservation, and confirmed RTSP progress. Every claim receives a monotonically
new ownership generation. A passive claim becomes confirmed only after an
expected successful response or a recognized WFD method. The active fallback
may atomically replace an unconfirmed claim, but it cannot replace a confirmed
session. A superseded handler cannot confirm, dispatch a later valid command,
or release the newer owner because all three operations require its exact
generation token. Unverified peers still fail before obtaining any claim.

Regressions cover unverified admission, unconfirmed supersession, confirmed
session exclusion, stale confirmation and release, malformed traffic, stale
`PLAY` dispatch, fallback replacement, and early cancellation after confirmed
passive progress. The exact 25-patch reconstruction passes all 130 FluxCast
tests. Package revision 50 carries the engine-only change with guard API 9.
All 163 repository tests, staged Omarchy validation, production ShellCheck,
and git whitespace checks pass. Because successful receiver negotiation state
changed, a short GUI connect/stream/Stop run remains the hardware acceptance
gate; no live network operation was run during this remediation.

The audited revision-50 package and matching API-9 plugin controller were then
installed on the live host and exercised through the normal GUI against the
stock Fire TV. The session reached `streaming`, the user confirmed that it
worked, and Stop completed cooperatively. The controller returned to `idle`
with complete helper cleanup; media processes and the session P2P interface
were absent, NetworkManager remained active, and infrastructure Wi-Fi remained
connected. This closes patch 31's short receiver gate without claiming a soak
test.

### Supplicant interface and group ownership (2026-08-26)

The exhaustive review found that direct-supplicant discovery returned every
supplicant interface with the selected adapter merely sorted first. Normal
cleanup consequently sent Cancel and Disconnect to every returned interface.
Group discovery likewise accepted the first interface with a nonempty Group
property after Connect, without proving that the group belonged to the selected
receiver, adapter, or current session.

Production patch 32 restricts writable control operations to the exact selected
physical interface and its `p2p-dev-<interface>` control object. The session
resolves and retains one exact control path before Connect and uses only that
path during failure and ordinary cleanup. Before Connect it records the
selected peer's `Groups` property; afterward it accepts only a group that is new
for that peer, belongs to the selected physical adapter, and is the sole
attributable candidate. Missing ownership evidence and multiple candidates fail
closed. Enumeration of other interface objects remains read-only.

Regressions cover two adapters, a missing selected adapter, a pre-existing
group, a foreign-adapter group, an unrelated group, two ambiguous new owned
groups, exact-path cleanup, and failed-connect cleanup. The exact 26-patch
reconstruction passes all 137 FluxCast tests. Package revision 51 carries the
engine-only change with guard API 9. All 163 repository tests, staged Omarchy
validation, production ShellCheck, Python compilation, and git whitespace
checks pass. The exact-clean revision-51 artifact passes the no-root audit,
candidate installation/removal, and disposable revision-50 to revision-51
upgrade/removal lifecycle. Its SHA-256 is
`40b82c1161d5d1878718721ba66c7d8c314218c6a6a956d90ab4e216b0a592f3`.
Because successful supplicant group selection changed, a short GUI
connect/stream/Stop run remains the receiver acceptance gate; no live network
operation was run during this remediation.
