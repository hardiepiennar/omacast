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
directory below `/run/user/$UID`; it does not trust PID or command data from a
shared `/tmp` path. Stop uses the current session's user-owned marker and does
not request a second authorization.

The helper restores NetworkManager, temporary systemd-networkd configuration,
D-Bus policy, and any firewall rule during normal stop, controller failure, or
its bounded recovery timeout. Omacast also exposes a panel recovery action when
the unprivileged session owner disappears.

The detached user service holds a logind idle/sleep inhibitor while casting,
and the bar widget holds the matching Wayland idle inhibitor. Both are
process-owned rather than persistent settings and disappear on normal Stop or
forced owner death.

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
