# Omacast 0.1.0 marketplace checklist

## Release candidate complete

- [x] Stable plugin ID and semantic version in `manifest.json`.
- [x] Omacast marketplace, panel, tooltip, and CLI branding.
- [x] Native Omarchy bar panel and live pipeline telemetry.
- [x] First-run readiness gates scanning and casting on the complete supported
      engine/helper/host path; missing companion components show one contextual
      visible-terminal setup action rather than an opaque scan failure.
- [x] Semantic idle/connecting/streaming/recovery icon colors.
- [x] Shell-independent user-service ownership survives a shell restart.
- [x] Failed authorization and killed-owner states expose contextual recovery.
- [x] Active casts inhibit Omarchy idle and system sleep; normal Stop and
      killed-owner failure injection release the inhibitor.
- [x] Live session age and bounded 50-session private history.
- [x] Normal completion and stale recovery remove volatile live telemetry;
      archived telemetry is pruned with the 50-session event-history bound.
- [x] Start-pending UI covers the service/status race, prevents duplicate
      actions, and always exposes Cancel; the watchdog actively stops a launch
      that never publishes supervised state.
- [x] Immediate installed-controller start/cancel exercised five consecutive
      times across both sides of the state-publication race; every attempt
      returned to idle with no user service or sleep inhibitor left behind.
- [x] Super+Alt+C toggle command documented and installed on the development
      host; a second press closes the panel and stock Super+C Universal Copy
      remains available.
- [x] Existing plugin upgrades in place under `hardie.omarchy-cast`.
- [x] Real Omarchy CLI lifecycle exercised with a temporary unique-ID twin of
      the release payload: add with enable, official validation, fast-forward
      update, disable, explicit re-enable, disable, and removal all passed.
      Live `shell.json` returned byte-for-byte to its starting state and the
      production plugin remained unchanged. This does not replace the pending
      public-repository clean-account test.
- [x] Safe profile is the receiver-accepted 720p60 / 7 Mbps Matroska pipeline.
- [x] Production planning, probes, simulation, validation, and execution expose
      one mirror/Safe contract. The abandoned mode/profile controls and their
      obsolete persisted-defaults surface have been removed rather than hidden.
- [x] Controller unit suite and staged Omarchy manifest validation pass together.
- [x] Companion engine build recipe and privileged boundary are tracked.
- [x] Companion revision 37 installs an exact-path, `prepare`-only Polkit
      action for active local users; guard API 4 binds the request UID to
      `PKEXEC_UID`. Package integrity, policy introspection, no-prompt malformed
      request, and Doctor readiness pass on the development host.
- [x] Companion package revision 25 declares every command required by the
      supported Miracast path instead of relying on optional host state.
- [x] Companion package revision 25 rebuilt twice from exact implementation commit
      `9cf9086f`: both builds passed 96 engine tests and were byte-identical at
      SHA-256 `1b50312c98b071deda144e7499b15cc3e95a2f031639268d0f0fe052562e5b21`.
      Package metadata and both immutable guard helper payloads were verified.
- [x] Final local release artifact rebuilt from exact implementation commit
      `a174ba5d` after panel-state hardening; 96 engine tests passed, dependency
      metadata verified, and SHA-256 validation produced
      `2e802cc67164b5d2bc7af72f825cab1a4591f0d59ad46b0a6cedb9cd7ad4c659`.
- [x] Bootstrap and Arch recipe share one 22-patch series; a separate pinned-base
      reconstruction applied all patches and produced a clean tree.
- [x] Companion package rebuilt from a clean clone of release commit `f7465fc`:
      makepkg applied all 22 patches, passed 96 engine tests, and produced
      `fluxcast-omarchy-cast 0.1.5.r3.omarchy-24` with SHA-256
      `0d446bfa4816cc8a6181132e87105f21b6032b2e6b7bd2969d21a8d733f18fad`.
- [x] Exact-commit release builder and pinned-action GitHub workflows added;
      tagged builds use a fresh Arch container, checksums, source identity,
      artifact upload, GitHub provenance attestation, and immutable release
      assets. The public workflow remains unproven until the repository is
      published and a tag is pushed.
- [x] Release builder exercised twice at commit `c0eab756`: both host builds
      were byte-identical with SHA-256
      `9930794460dd6779f4fbe9620a88177e39fad0b4f6baf1730b59d8186a2caa4e`.
      The workflow's fresh Arch container path
      also passed all 96 engine tests and source/checksum validation, producing
      SHA-256 `1b1cb8c6d662b905a09d3f0826ed8043b3d4aa7185908469dcbbbd1a1cec701b`.
      Hashes differ across those environments because `.BUILDINFO` records the
      dependency set; provenance binds the published environment and commit.
- [x] Local development build of revision 26 applied all 23 patches, passed 97
      engine tests, contained both guard helpers, and produced SHA-256
      `121758cd3d5d7bd4366f0fc95f26708db9408d2827b956bdfbac3d7c459a42ef`.
- [x] Exact-clean release builder cloned implementation commit `320c0f8`,
      reapplied all 23 patches, passed 97 engine tests, and produced revision 26
      with SHA-256
      `336659ea5eade1033fb54b062bec607be0c6b86c7508c8f1b9422f9c15704182`.
- [x] Development readiness rejects the actual revision-24 installation as
      incompatible using both the patch-23 engine flag and guard API revision 2,
      so an independently updated UI cannot cross an old privileged contract.
- [x] Exact-clean revision 27 rebuilt from commit `b716063`; all 23 patches and
      97 engine tests passed, its packaged guard reports API revision 2, and
      SHA-256 is
      `9d1173e6fe5d3d0563b0eedd8cf86cdf79f3631b7a5cbede6d5f2a6a9bb2e0c4`.
      The no-root artifact audit also passes archive safety, dependencies,
      permissions, helper contract, engine flags, and pacman integrity.
- [x] Disposable pacman lifecycle installed revision 25, verified all 191 files,
      upgraded in place to revision 27, verified all 191 files and helper API
      revision 2, then removed it with no package files or symlinks left behind.
- [x] Privacy-only revision 28 rebuilt from exact commit `3dc69a0`: all 23
      patches and 97 engine tests passed. The artifact audit and disposable
      revision-27 → revision-28 upgrade/removal lifecycle passed; SHA-256 is
      `62b3233f8dcb0a2ddcfefd1fbced8b3a8d020272e694023cee3d3f00226bec50`.
- [x] Revision 29 adds fixed-allowlist volatile telemetry cleanup to the
      independent recovery path. The exact-clean build from commit `f3c2faf`
      applied all 23 patches and passed 97 engine tests. Artifact audit,
      candidate-only lifecycle, and disposable revision-28 to revision-29
      upgrade/removal passed; SHA-256 is
      `dbe160b213a5dc49bd809f6f2bd825eac36def21f0405956d7bfdc2526512259`.
      Live forced-owner validation remains pending.
- [x] Revision 30 replaces ambiguous privileged-helper boolean dispatch with
      explicit fail-safe control flow and adds a production-only ShellCheck
      release gate. The exact-clean build from commit `ee522b8` applied all 23
      patches and passed 97 engine tests. Artifact audit, candidate-only
      lifecycle, and revision-29 to revision-30 upgrade/removal passed;
      SHA-256 is
      `40d77709b9fc2c17fd1053f69c0656f5d406c2a365dbcbf0e37ca02dd9085a3f`.
      All 102 offline controller/packaging tests and the official Omarchy
      validator also pass.
- [x] Revision 31 adds contextual active-window casting, explicit receiver
      choice, and an Omarchy-style keyboard cursor. Exact source commit
      `66ab7e4` applied all 24 patches and passed 98 engine plus 109
      controller/plugin tests. The no-root artifact audit, candidate-only
      lifecycle, and revision-30 to revision-31 upgrade/removal lifecycle
      passed; SHA-256 is
      `637f96439e5da63a4ef86300a245cd17f30da2054ec9cd7c3c23e7bfc094af6d`.
      A temporary fake-controller plugin twin loaded in the real shell with no
      QML errors; one Enter emitted exactly one window-source start request.
- [x] Revision 32 supports a legitimately active systemd-networkd service and
      preserves helper setup errors across the elevated-process cleanup race.
      Exact source commit `c716276` applied all 24 patches and passed 98 engine
      tests. The no-root audit, candidate-only lifecycle, and revision-31 to
      revision-32 upgrade/removal lifecycle passed; SHA-256 is
      `f22e2fa959bfb277b12b10b8678e439438d38e7090794c7968aa4bf5f9e33e67`.
      The live upgrade reports 191 package files with zero alterations and
      helper API revision 3. Its first guarded retry crossed the previously
      failing networkd setup boundary and restored the recorded network state.
- [x] Revision 33 (superseded research candidate) replaced GSR's incompatible
      portal route with typed,
      SHM-capable GStreamer PipeWire capture feeding FFmpeg VAAPI, and requires
      encoded video progress before green streaming state. The local portal
      probe accepted real window SHM frames and started VAAPI. Exact-clean build
      commit `3e9ad8b` passed 101 engine tests, the no-root audit, fresh
      install/removal, and revision-32 to revision-33 upgrade/removal. SHA-256
      is `3afbf0e6dddc96cdcdcb823383ce410f6f48ac4f1bdfeaa6aec4be0dbcb5ca63`.
      The live revision-32 to revision-33 upgrade then passed with 191/191
      package files intact, compatible engine capabilities, all doctor
      dependencies present, an in-place plugin update, and Omarchy validation.
      Receiver acceptance was intentionally cancelled when revision 34 removed
      portal/window capture from the release product.
- [ ] Revision 34 removes portal/window capture, the screenshot preview, and
      their GStreamer dependencies; restores the display series; and
      keeps encoded-frame proof. Exact-clean commit `e269955` passed 97 engine
      tests, 105 controller/plugin tests, Omarchy validation, the no-root audit,
      fresh install/removal, and revision-33 to revision-34 upgrade/removal.
      SHA-256 is
      `66822a6349f1cda75edd1046e0b2afe189f7b485733e3186d163db4ad87fcb07`.
      The live revision-33 to revision-34 upgrade passed with 191/191 package
      files intact, no GStreamer dependencies, no public portal-source flag,
      a ready doctor contract, the matching revision-34 plugin, one configured
      widget, and clean Hyprland validation. A live Fire TV run then passed
      discovery, negotiation, encoded-frame proof, internet coexistence, and
      exact cleanup, but failed subjective motion/audio acceptance because the
      user saw stutter and heard glitches.
- [x] The controlled Fire TV Matroska/FLV A/B is complete. FLV was materially
      worse and became unwatchable despite its superior offline loopback packet
      cadence, so it remains diagnostic-only and is rejected as the default.
- [ ] Privileged live failure injection remains pending.
- [x] Revision 35 added patch 26's private MPEG-TS GSR handoff candidate while
      preserving Matroska as the default and excluding superseded patches
      24–25. Under sustained offline motion it reduced timestamp-change gaps at
      or above 50 ms from 202 to seven, with zero gaps at or above 75/100 ms,
      zero missing RTP sequence numbers, and steady 30–31 fps GSR capture. All
      105 controller/plugin tests and all 98 reconstructed engine tests pass.
      Exact-clean commit `b8068a4` passed the same 98 engine tests, checksum,
      no-root audit, fresh install/removal, and revision-34 to revision-35
      upgrade/removal. SHA-256 is
      `7748026367a791766261b34256438fd88c0f68d3a8645ffcb193558bdcd20938`.
      The audited live upgrade then passed with 191/191 files intact, compatible
      engine capabilities, ready doctor output, a matching clean plugin clone,
      and staged/live Omarchy validation. Receiver A/B then found the MPEG-TS
      path still visibly stuttery, so revision 36 excludes patches 23–26 from
      production while preserving them as research.
- [x] Revision 36 promotes the receiver-accepted 720p60 Matroska profile,
      removes private FPS/handoff overrides, condenses Nerd Mode, and ships
      only the 20 receiver-relevant patches (1–6 and 9–22). Exact-clean final
      UI commit `ce79b3e` passed 104 controller/plugin tests, staged Omarchy validation,
      94 reconstructed engine tests, checksum and no-root artifact audits,
      fresh install/removal, and revision-35 to revision-36 upgrade/removal.
      SHA-256 is
      `761726db6fa551dc36f0fa777070ade8a399e8c5c0b2373125b3a96f44fbca32`.
      The audited package then upgraded the live host with all 191 files intact;
      the matching clean plugin clone validates, is the only configured widget,
      and Hyprland reports no configuration error. The pre-upgrade 720p60
      process remained active and healthy throughout the file update.
      A fresh normal revision-36 launch then negotiated 1280x720p60 with no
      diagnostic or alternate-handoff flags, settled at 59.22–60.48 measured
      fps with zero drops/duplicates and healthy telemetry, and passed the
      user's visual and audio check. Normal Stop restored Wi-Fi, disconnected
      P2P, released the service/inhibitor/processes, and removed that session's
      runtime and telemetry directories.
- [x] Personal receiver label and radio address are absent from public tracked
      sources; retained lab launchers require explicit receiver/interface/output
      inputs while preserving the historical procedure.
- [x] Publication privacy audit covers the current snapshot, commit/tag
      identities, historical images, release package metadata, private network
      observations, and recognizable credential signatures. Publication uses
      a parentless sanitized history and a non-personal noreply identity.
- [x] Changelog, installation, privacy, DRM, limitations, and license documented.
- [x] Root marketplace preview uses the genuine healthy receiver-backed Nerd
      Mode panel in a 16:9 privacy-safe composition matched to the marketplace's
      desktop card geometry. The original panel pixels remain unchanged and
      contain no receiver, radio, network, notification, or desktop identity.
- [x] README Nerd Mode preview captured from a genuine healthy receiver-backed
      720p60 session. The panel-only crop shows live health-colored telemetry
      while excluding the receiver name, radio address, network name, and
      desktop content.
- [x] Root README documents companion dependencies and explicit removal.
- [x] Removal documentation explains the intentionally retained private
      preferences/history and offers a recoverable Trash command for users who
      want to erase both.
- [x] Root security policy documents privilege, cleanup, data exposure, and
      private vulnerability reporting.
- [x] Marketplace review at exact public commit `965f94d` is addressed with
      bounded subprocess/controller output, a genuinely streaming capped QML
      collector, bounded receiver/readiness/warning and session models,
      plain-text rendering, and pre-parse state size/shape checks. The adjacent
      telemetry stat/read race and FluxCast diagnostic sink were hardened
      without changing the guard, network, or media path.
- [x] Post-review smoke test installed exact remediation commit `4ec0f62` in
      the real Omarchy shell, validated a clean QML load, discovered the waiting
      receiver, negotiated `1280x720p60`, and held healthy 59.5–60.5 measured
      fps at approximately realtime with zero FFmpeg drops or duplicates. A
      cooperative Stop reported complete owned-session cleanup, restored idle,
      removed current runtime/telemetry, left Wi-Fi connected, and released the
      user service and media processes. No subjective motion/audio verdict was
      requested during this short smoke test.
- [x] Follow-up review at `540f578` reproduced a FIFO replacement blocking the
      state reader before descriptor validation. State and current telemetry
      now open nonblocking, and a deadline-bounded subprocess test proves the
      FIFO is rejected as non-regular without hanging.
- [ ] Follow-up review at `540f578` also found that the passwordless guard could
      apply negative nice to a same-user process selected through a user-owned
      PID file and spoofable identity labels. Version 0.1.1 removes that channel,
      advances companion revision 41 to guard API 7, applies CPU weight only
      through the user-owned transient service, and pins bounded heartbeat reads
      to one verified descriptor. User markers are anchored below a root-owned
      parent and the unused privileged Stop verb is gone. Revision 38 passed
      package and receiver acceptance before the adjacent races were found.
      Revision 40 then passed streaming but left its down P2P client after Stop;
      revision 41 records and removes only session-owned client devices.
      Complete the exact revision-41 package and receiver lifecycle before
      closing this item.
- [x] Proposed marketplace metadata: category `Hardware`; tags `bar`, `media`,
      and `quickshell`. These match the closest current Hardware/media peers and
      describe the user-facing plugin more precisely than duplicating Hardware
      with the generic `system` tag.
- [x] Plugin ID `hardie.omarchy-cast` is absent from active and retired IDs in
      marketplace registry commit `c9f6a5e`; recheck immediately before filing.
- [x] Current marketplace main commit `5acd4d3` was rechecked on 2026-08-23:
      neither `hardie.omarchy-cast` nor Omacast appears in the 40,785-line
      active/retired registry. The submission contract still accepts Hardware
      with bar/media/quickshell and now requires `approved-and-verified` for a
      new exact-commit listing.
- [x] Marketplace security baseline v3 rerun locally using exact upstream
      scanner commit `c9f6a5e` against post-redaction Omacast commit `d9d5f70`:
      42 relevant
      files, `review-required`, zero findings, and `blocksApproval: false`.
      Expected review capabilities remain privilege, package management, and
      service management. The official GitHub-snapshot run remains a publishing
      gate because this repository is not public yet.
- [x] The same baseline logic was refreshed against current commit `e3dce26`:
      42 relevant files, zero findings, `review-required`, and
      `blocksApproval: false`, with the same three expected capabilities.
- [x] After production-scope consolidation, the same baseline logic scanned 42
      relevant files at exact revision-30 source commit `ee522b8`, found zero
      findings, and retained the expected `review-required` result with
      `blocksApproval: false` and the same three review capabilities.
- [x] The baseline-v3 logic was rerun after the revision-31 UI and portal-source
      work at commit `4ceef58`: 42 relevant files, zero findings,
      `review-required`, `blocksApproval: false`, and only the same expected
      privilege, package-management, and service-management capabilities.
- [x] Marketplace main `5acd4d3` baseline-v3 logic was run locally against exact
      current Omacast commit `f272e9f`: 37 production/research runtime text files,
      zero findings, `review-required`, `blocksApproval: false`, and only the
      expected privilege, package-management, and service-management
      capabilities. A public GitHub snapshot remains mandatory at submission.
- [x] Official-format marketplace issue body prepared in
      `docs/marketplace-submission.md`; its ownership/checklist boxes remain
      deliberately unchecked pending the public URL and explicit owner review.
- [x] The marketplace V3 scanner from exact marketplace commit
      `55f3491b665e72e72ad12ec8718ee49609db09b6` was run over the tracked local
      snapshot with the marketplace's real scope rules. It reports
      `review-required`, zero findings, and only the expected non-blocking
      `privilege`, `package-manager`, and `service-management` capabilities.
      There is no unpinned remote-execution or passwordless-sudoers finding.

## Publishing actions

- [ ] Push the repository to its permanent public git URL.
- [ ] Publish a trusted companion Arch package or signed release artifact.
- [x] Replace `<repository-url>` in the README with the permanent URL.
- [ ] From a clean Omarchy account, run add, enable, Super+Alt+C summon, update,
      disable, and remove using the permanent public repository; the equivalent
      local real-shell lifecycle has passed with an isolated plugin ID.
- [ ] Complete the documented 30-minute repeatability and forced-cleanup gates
      before describing 0.1.0 as broadly supported rather than a release
      candidate.
- [ ] Replace the URL in `docs/marketplace-submission.md`, review its body,
      confirm ownership and every submission checkbox, then explicitly approve
      creating the listing issue.

## Production and competition finish

- [ ] Cold-boot and service-restart acceptance: the first panel cast succeeds
      without a recurring administrator prompt and with no manual Wi-Fi,
      P2P, DHCP, D-Bus, or `/run/systemd/network` repair.
- [ ] Failure-inject authorization cancellation, absent runtime network setup,
      failed DHCP, rejected P2P negotiation, and stale P2P cleanup; each case
      has a distinct actionable panel error and restores the exact prior
      NetworkManager/systemd-networkd service and socket state.
      Stable controller codes and state/history propagation are covered by
      offline tests, including dismissed authorization as a no-change result;
      privileged and receiver-backed injections remain open.
- [x] Receiver-backed revision-37 acceptance verifies passwordless prepare,
      click/Enter-to-cast, keyboard N/Q controls, panel toggle close, automatic
      TV-side disconnect, and exact cleanup. Passwordless prepare, click start,
      keyboard/UI review, healthy 720p60 streaming, shell survival, and the
      automatic disconnect path passed. Receiver-side exit produced a
      `receiver disconnected` completion, returned idle, restored infrastructure
      Wi-Fi and prior networkd state, and left no service, inhibitor, media
      process, or P2P interface. Offline policy, controller, and QML contract
      tests pass.
- [ ] Normal panel sessions cast until explicitly stopped. A renewable,
      ownership-checked lease permits indefinite healthy use while missed
      heartbeats, logout, engine/controller/helper death, suspend, and normal
      Stop still trigger bounded idempotent cleanup.
- [x] Optional Nerd Mode keeps the default live view to four calm rows and
      expands into cadence, scheduler, RTP, radio, timing, and peaks. Real-shell
      simulated renders verified both layouts and honest unavailable states;
      opening it does not enable the default-off packet trace.
      Release cleanup uses compact labels and values and reports a health flag
      count rather than expanding raw issue strings.
      Final polish uses a two-column card grid and hides unavailable deep-probe
      cards instead of spending space on dead measurements.
- [x] Release discovery exposes only the receiver class that passed the 0.1.0
      hardware gate. Generic WFD advertisements are not presented as usable;
      panel start snapshots its selected receiver and exposes launcher failure
      text rather than silently returning to idle.
- [ ] Optional smooth-playback buffering remains research-only. Compare the
      current no-extra-buffer baseline with a bounded adaptive candidate; ship
      no toggle until receiver tests prove a repeatable benefit and report its
      latency cost honestly.
- [x] Superseded product gate: portal window selection passes its
      receiver-backed audio, pacing, privacy,
      cancellation, and teardown gate before it is advertised as supported.
      The first correctly targeted Fire TV run reached PLAY, then proved GSR
      cannot consume the portal's SHM fallback. Revision 33 instead uses typed
      GStreamer PipeWire capture into the FFmpeg VAAPI Safe path and fails
      closed when Omarchy's picker returns its default Outputs tab. Super+C now
      tells users to open Windows. Region and virtual sources remain
      unimplemented. Revision 34 removed this mode instead of claiming support.
- [x] Superseded product gate: a local-only source preview is visually polished,
      never archived, handles
      protected/unavailable content honestly, and measurably does not degrade
      the cast.
      Implementation and real-shell visual acceptance now pass: native
      ScreencopyView captures one cursor-free frame, labels unavailable or
      protected content honestly, writes no file, and receives a null source as
      soon as the panel closes or start-pending/session state begins. Final
      receiver telemetry would have needed to confirm the path. Revision 34
      removed the preview, so it no longer consumes compositor resources.
- [ ] Bar icon and tooltip are verified across idle, scanning, preparation,
      connecting, streaming, stopping, error, recovery, shell reload, and lost
      ownership; color is never the only state cue.
- [ ] Revision 42 authenticates passive and active RTSP peers as the selected
      receiver on the session-owned P2P interface before negotiation or
      capture, admits only one receiver, and fails closed on ambiguous identity
      or conventional-address fallback. The exact 22-patch reconstruction and
      all offline suites pass; a short selected-Fire-TV connect/Stop run remains
      the receiver-backed release gate.
- [x] Revision 43 removes user-owned telemetry from the independent root
      recovery helper. Normal and explicit stale recovery retain unprivileged
      cleanup; source and built-package contracts reject `/run/user/` and live
      telemetry names in the root recovery payload. All offline tests and staged
      validation pass; the media and network paths are unchanged.
- [x] Session events, history, and Stop requests use validated directory and
      file descriptors, bounded nonblocking reads, single-link ownership checks,
      unpredictable atomic Stop temporaries, and strict controller-issued
      session IDs. Adversarial and lifecycle tests pass; no privileged, media,
      or network behavior changes.
- [x] Runtime state and live/archive telemetry stay relative to validated
      private directory descriptors for reads, atomic replacement, cleanup,
      retention, and append. FluxCast writes progress, latency, packet, and log
      output through preopened descriptor-backed paths. Link, FIFO, hard-link,
      and induced directory-replacement tests preserve unrelated targets.
- [x] The unprivileged renewable heartbeat opens its private parent and file
      with no-follow/nonblocking semantics, validates type, ownership, mode,
      size, and link count before truncation, and renews one pinned descriptor
      until Stop. FIFO, link, oversized/public-file, unsafe-parent, and
      post-open replacement regressions pass without changing unrelated data.
- [x] Production patch 28 bounds every receiver-facing RTSP line, header set,
      and body; rejects malformed or truncated lengths; times out negotiation
      and partial messages; and limits passive workers to four. The exact
      reconstruction and offline adversarial suite pass. Revision 44 also
      completed a GUI Fire TV connect/stream/Stop run with user-accepted picture
      and sound, idle controller state, removed P2P client, and connected Wi-Fi.
- [ ] Unlimited casts retain an 8 MiB persistent archive, a 256 KiB recent
      engine-log tail, and one latest FFmpeg progress record while live status
      continues. The production packet-trace override is absent. Flood and
      quota tests, exact reconstruction, offline FFmpeg progress smoke, clean
      revision-45 package build, no-root artifact audit, and disposable
      install/removal pass. A short receiver connect/Nerd Mode/Stop run remains
      required because the supported media process now drains FFmpeg progress
      through a pipe.

Run this before every release:

```bash
scripts/test
scripts/build-release-artifact
scripts/audit-release-artifact
scripts/test-package-lifecycle CANDIDATE.pkg.tar.zst
scripts/test-package-lifecycle BASELINE.pkg.tar.zst CANDIDATE.pkg.tar.zst
git diff --check
```

The ignored `work/` directory is a local research checkout and is intentionally
not part of the marketplace payload. Validate through `scripts/validate-plugin`
or from a clean clone; do not delete research evidence merely to validate the
working tree root.
