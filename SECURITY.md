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

The helper binds the requested UID to Polkit's authenticated `PKEXEC_UID`, then
accepts a bounded, validated argument contract. It stores privileged
state under `/run/omarchy-cast` and exchanges session signals through a private
user-owned marker directory nested beneath the root-owned session directory.
The user can write marker contents but cannot replace that directory entry or
redirect root's ownership changes. The helper accepts no process identifier,
scheduling request, path, or privileged Stop action from the unprivileged
session. Stop writes the current session's user-owned marker directly and does
not request a second authorization.

The helper restores NetworkManager, temporary systemd-networkd configuration,
D-Bus policy, and any firewall rule during normal stop, controller failure, or
its bounded recovery timeout. Omacast also exposes a panel recovery action when
the unprivileged session owner disappears.

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

The runtime `state.json` file is limited to 65,536 bytes and the current
telemetry snapshot to 262,144 bytes. Both are opened nonblocking without
following symlinks, checked through the same descriptor for regular-file
ownership and private permissions, read only to their limit plus one byte, and
checked for bounded JSON depth, fan-out, node count, and string length before
use. Nonblocking open ensures a FIFO or other non-regular replacement reaches
descriptor validation instead of stalling the controller. Receiver and
controller-derived strings use plain-text rendering in the panel.

## Data and display exposure

Mirroring sends the selected desktop output and its audio to the chosen local
Miracast receiver. Anything visible or audible on that output—including
notifications—may be disclosed to people near the receiver. Omacast does not
send telemetry to an internet service. Runtime state and diagnostics are kept
in private per-user directories, with history bounded to the newest 50
sessions.

Removing the plugin or companion package does not silently erase user data.
Legacy development preferences under `~/.config/omarchy-cast` and bounded
diagnostic history under `~/.local/state/omarchy-cast` remain private and can be
moved to Trash using the explicit command in the README.

## Reporting a vulnerability

Do not include credentials, private display content, or sensitive logs in a
public report. Once the permanent public repository is established, use its
private security-reporting channel. Until then, contact the maintainer through
the same private channel used to obtain this source.
