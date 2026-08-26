# FluxCast companion package

This recipe builds the exact FluxCast revision used by the Omacast
research and applies the tracked compatibility, capture, timing, telemetry,
and audio synchronization patches during package
build. It does not read or install the ignored `work/` directory.

From a clean clone of this repository:

```bash
cd packaging/arch
makepkg -si
```

Maintainers should use `scripts/build-release-artifact` from the repository
root. It refuses a dirty tree, clones the exact source commit into a temporary
build directory, and emits the package, checksum, package metadata, and source
commit under `dist/`. The tagged GitHub release workflow runs that builder in a
fresh Arch Linux container and attests the package provenance.

Arch dependencies include `python-dbus-next` and `python-gobject`, plus FFmpeg,
GPU Screen Recorder, NetworkManager, wpa_supplicant, iw, PipeWire's PulseAudio
client, polkit, systemd, and iproute2. The packaged command exposes only the
Miracast/WFD engine. Unused tray, Chromecast, DLNA, and LAN streaming modules
and their Python dependencies are excluded from the wheel.

The package provides the patched streaming engine, three immutable helper
executables under `/usr/lib/omarchy-cast/`, and an exact-purpose Polkit action.
The action permits only the helper's `prepare` command for the active local
user; the helper additionally requires the requested UID to match Polkit's
authenticated caller. The controller supplies a controller-issued session ID,
a discovered interface, the calling UID, and a 60-second renewable safety lease. A healthy controller
renews that lease while the cast runs; a missed lease triggers the independent
cleanup path, so normal sessions can run until explicitly stopped without
making privileged network state unbounded. The guard generates a per-session
runtime DHCP match, opens only P2P TCP 7236 if UFW is active, and starts a
root-owned broker whose user-accessible socket accepts only fixed `connect` and
`cleanup` operations for the selected adapter, receiver, and frequency. It
does not grant the desktop user's UID general wpa_supplicant D-Bus access. The
helpers remove their runtime state, restore NetworkManager, and restore the
exact prior systemd-networkd service/socket state on every exit path. If the
volatile
`/run/systemd/network` directory is absent after boot, the helper creates it
with fixed root ownership and removes it again only if Omacast created it. No
persistent network, D-Bus, or firewall rule or workstation-specific setting is
installed. The package-owned Polkit action is declarative and is removed with
the package.

The primary helper exposes an unprivileged JSON `--version` probe. Omacast
requires guard API revision 10 and the matching FluxCast capability set before
enabling discovery or Cast, so independently updated marketplace UI cannot
cross an older privileged-helper contract.

API revision 10 creates only the protected session identity first. Independent
recovery validates that identity and publishes a root-owned readiness marker;
the primary helper refuses to create temporary network state or start the
supplicant broker until that acknowledgement arrives within its bounded
startup window.

Media scheduling does not cross the Polkit boundary. The plugin's existing
user-owned transient service applies a CPU weight to its own supervised process
tree; the root helper accepts no PID and does not call `renice`.

To update the engine, rebuild from a clean package directory. Do not apply
patches to a system-installed engine at runtime.
