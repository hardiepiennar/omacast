# Security

## Privileged boundary

Omacast's QML panel and Python controller run as the desktop user. They do not
install packages, edit Omarchy configuration, or receive general root access.
Starting a hardware cast invokes the fixed
`/usr/lib/omarchy-cast/omarchy-cast-guard` helper through `pkexec`. The
companion package installs a declarative Polkit action that permits only that
exact executable with `prepare` as its first argument, and only for the active
local user. Casts therefore need no recurring password prompt. Installing or
updating the companion package still requires explicit administrator approval.
A second exact `reclaim` action always requires administrator approval. It is
used only from explicit Restore when a down, disconnected P2P client blocks
the normal fail-closed baseline; it accepts no interface other than the
validated managed adapter selected by the controller and refuses to run while
any protected Omacast root session exists. Prepare and reclaim hold the same
root-owned runtime-directory lock, preventing either operation from racing the
other's ownership check.

The helper binds the requested UID to Polkit's authenticated `PKEXEC_UID`, then
accepts a bounded, validated argument contract. It stores privileged
state under `/run/omarchy-cast` and exchanges session signals through a private
user-owned marker directory nested beneath the root-owned session directory.
The user can write marker contents but cannot replace that directory entry or
redirect root's ownership changes. The helper accepts no process identifier,
scheduling request, path, or privileged Stop action from the unprivileged
session. Stop writes the current session's user-owned marker directly and does
not request a second authorization.

Code already running as the active desktop user can request or renew casting
preparation and may therefore interrupt that user's ordinary networking. This
is an accepted same-session denial-of-service risk: requiring authentication
would restore a password prompt for each cast, while the active user already
controls their NetworkManager connections and Omacast process. Inactive and
remote users remain denied, and a lost renewal invokes bounded recovery. The
action does not provide a general privileged command or accept a user-selected
executable.

During normal Stop, controller failure, or its bounded recovery timeout, the
helper attempts every safe restoration step for NetworkManager, temporary
systemd-networkd configuration, and any firewall rule even if an earlier step
fails. Incomplete cleanup retains root-owned recovery evidence and is reported
as recovery state instead of being silently treated as success. Omacast also
exposes a panel recovery action when the unprivileged session owner disappears.
The offline failure-injection suite covers partial initialization and
independent restoration failures; the remaining receiver-backed privileged
failure matrix is tracked in the release checklist.

The helper does not install a per-user system-bus policy. Instead, a root-owned
session broker exposes only fixed `connect` and `cleanup` requests through a
private socket, with the adapter, receiver, and frequency pinned by the
authenticated guard request. The broker and recovery helper clear WFD metadata
only while its exact value and root-owned marker still prove Omacast ownership.
The guard records P2P clients created after its clean baseline in root-owned
session state and removes only those recorded devices automatically. Explicit
administrator-approved reclaim prevalidates every matching device before any
deletion, repeats the safety check immediately before each removal, and accepts
only down, disconnected P2P clients without IPv4 or global IPv6 addresses.

The detached user service holds a logind idle/sleep inhibitor while casting,
applies a user-owned CPU weight to its complete supervised process tree, and
the bar widget holds the matching Wayland idle inhibitor. No privileged process
scheduling action exists. The process-owned inhibitor and service weight
disappear on normal Stop or forced owner death.

## Runtime data boundaries

Host discovery drains stdout and stderr concurrently but retains at most
65,536 bytes from either stream. The controller emits at most 262,144 bytes in
one UI response. The panel uses a streaming parser instead of Quickshell's
complete-output collector, retains at most 262,144 characters, and rejects an
oversized response before `JSON.parse`. A streaming discard parser drains each
controller stderr channel without retaining or displaying its contents.
Receiver, monitor, readiness, warning, diagnostic, and telemetry collections
are normalized to small allowlisted models with explicit count and string
limits before QML displays them.

The companion engine retains at most 128 KiB from either stdout or stderr of
each internal command and terminates a command that exceeds that limit.
Receiver-advertised RTP ports are limited to five decimal digits and the valid
0–65535 protocol range before integer conversion. The live latency journal is
private, descriptor-validated, and capped at 256 KiB; an unanswered periodic
RTSP keepalive occupies at most one pending entry.

The runtime `state.json` file is limited to 65,536 bytes and the current
telemetry snapshot to 262,144 bytes. Both are opened nonblocking without
following symlinks, checked through the same descriptor for regular-file
ownership and private permissions, read only to their limit plus one byte, and
checked for bounded JSON depth, fan-out, node count, and string length before
use. Nonblocking open ensures a FIFO or other non-regular replacement reaches
descriptor validation instead of stalling the controller. Receiver and
controller-derived strings use plain-text rendering in the panel.

## Developer package tools

`scripts/audit-release-artifact` and `scripts/test-package-lifecycle` inspect a
release candidate by executing or installing some of its content as the
invoking user. They are not hostile-package sandboxes. Both require the
`--trusted-local-artifact` acknowledgement and must be used only with a package
built from the clean source commit under review. Checksums prove identity after
that build; they do not make an untrusted package safe to execute.

## Data and display exposure

Mirroring sends the selected desktop output and its audio to the chosen local
Miracast receiver. Anything visible or audible on that output—including
notifications—may be disclosed to people near the receiver. Omacast does not
send telemetry to an internet service. Runtime state and diagnostics are kept
in private per-user directories. Event history and telemetry archives retain
only the newest 50 sessions; each persistent telemetry archive stops at 8 MiB,
the recent engine-output tail retains at most 256 KiB, and live FFmpeg progress
retains only its latest complete record.

Removing the plugin or companion package does not silently erase user data.
Legacy development preferences under `~/.config/omarchy-cast` and bounded
diagnostic history under `~/.local/state/omarchy-cast` remain private and can be
moved to Trash using the explicit command in the README.

## Reporting a vulnerability

Do not include credentials, private display content, or sensitive logs in a
public report. Use GitHub's
[private vulnerability reporting form](https://github.com/hardiepiennar/omacast/security/advisories/new)
for this repository.
