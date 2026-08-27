# Agent handoff

Before changing this repository, read:

1. `docs/architecture-and-roadmap.md` — canonical product, architecture,
   sequencing, safety, and acceptance plan.
2. `docs/research-log.md` — detailed evidence and experiment history.
3. `README.md` — concise current status and entry points.

Rules for future work:

- Treat `scripts/lab/phase1*`, `meta/`, and `patches/` as proven research
  artifacts until a tracked production replacement exists. Do not casually
  rewrite or delete them.
- `work/` is ignored and cannot be a production dependency.
- Do not hard-code this workstation's Wi-Fi interface, monitor, receiver MAC,
  home path, channel, subnet, GPU render node, or audio source in production
  code.
- Resolve radio/internet coexistence and reproducible FluxCast delivery before
  investing in the QML plugin UI.
- Keep QML presentation separate from unprivileged session orchestration and
  privileged networking.
- All temporary system/network/display changes must have ownership checks,
  bounded recovery, idempotent cleanup, and failure-injection tests.
- Use Omarchy 4's root `manifest.json` schema and run
  `omarchy plugin validate .` once plugin files exist.
- Do not run a live Fire TV/network test until the user confirms that the TV is
  waiting in Display Mirroring and understands the temporary network impact.
- Never start an interactive `sudo` password prompt inside a hidden command
  session. For an action that needs user authorization, use a visible desktop
  authorization flow such as `pkexec`, or ask the user to run the command in a
  terminal they can see.
- After installing or upgrading the companion package for a live test, refresh
  the installed Omarchy plugin from the current checkout, reload the shell, and
  run readiness through the installed panel/controller path. A repository-local
  `PYTHONPATH=src` doctor result alone does not prove that the GUI is current.
- Preserve conflicting evidence in the research log; mark conclusions
  superseded instead of rewriting history.
- Update the canonical plan and acceptance evidence as decisions change.
- Keep marketplace and review comments terse and human: state the relevant
  change, exact commit, and requested action. Do not add test-result recitals,
  promotional detail, or release-summary prose unless the reviewer requires it.
- For an unpublished submission under review, update the existing submission
  issue body to name the full current remote HEAD while preserving its required
  headings and checked checklist. Once the plugin is published, do not reuse
  or edit the closed submission to publish a new commit: use the marketplace's
  newer-upstream plugin-verification workflow with the exact tested HEAD.

Pre-production release gate:

- Before calling a candidate production-ready, trace every lifetime clock from
  QML through the controller, transient user and system services, privileged
  guard and recovery process, broker, engine, and RTSP/media protocol. Classify
  each as a startup deadline, renewable failure lease, shutdown bound, or
  session wall clock. A cast advertised as "until stopped" must have no fixed
  wall-clock limit in any healthy nested process or service.
- Add a regression for every lifetime defect at the layer that caused it. The
  suite must prove that a healthy renewable session survives beyond every
  former cutoff and that owner death, missed heartbeat, Stop, and cleanup still
  finish within their documented bounds. A longer fixed timeout is not a fix.
- Acceptance evidence must postdate the production architecture it is meant to
  validate. A long soak performed before a helper, broker, service, recovery,
  media, or network-lifecycle change cannot validate the changed stack. Run the
  canonical repeated soak and failure-injection gates on the exact installed
  release candidate.
- An urgent compatibility or breaking-defect release may defer broader soak or
  failure-injection coverage only when the maintainer explicitly records that
  scope decision, the exact installed candidate passes a direct regression and
  normal cleanup test, product claims remain conservative, and the deferred
  gates stay tracked for the next reliability release. Never report deferred
  coverage as passed.
- When companion behavior changes incompatibly, bump its helper API and package
  revision, update controller readiness and artifact/lifecycle tests, and prove
  that the new plugin rejects the old installed helper. Test through the
  installed plugin/controller path, not only repository-local imports.
- Before publishing, reconcile `manifest.json`, `pyproject.toml`, the changelog,
  README URLs, helper API, package revision, tag, artifact metadata, and
  marketplace target SHA. Confirm that every documented release URL and asset
  exists and installs the required API; passing source tests does not prove a
  usable distribution path.
- Run the final timer search, clean-clone build, controller/plugin/engine tests,
  shell lint, plugin validation, artifact audit, disposable install/upgrade/
  removal, installed readiness check, and required receiver acceptance against
  one exact commit. Any code or packaging change after that invalidates the
  candidate and requires the proportionate gates to be rerun.

Security review checklist:

- Treat wireless metadata, subprocess output, controller JSON, runtime files,
  and every predictable same-UID path as untrusted input.
- Bound bytes before parsing or retaining them. Also cap JSON depth, nodes,
  collection counts, and strings; project only allowlisted fields into QML and
  render non-constant text with `Text.PlainText`.
- For predictable runtime files, open with `O_NOFOLLOW`, `O_NONBLOCK`, and
  `O_CLOEXEC`; anchor child opens to validated parent directory descriptors.
  Use `fstat` on the opened descriptor to check type, owner, mode, size, and
  link count as applicable, then read, lock, or apply permissions only through
  that same descriptor. Never check a path and then reopen or `chmod` it.
- Validate a file before truncating or writing it. Prefer private atomic
  temporary files plus replacement for state, and reject symlinks, hard links,
  FIFOs, sockets, devices, public modes, and unexpected owners.
- Never let a privileged helper act on a user-owned PID or path, or trust
  process names and command lines as authorization. Root actions must use a
  fixed-purpose API and identity/ownership established by the privileged side.
- When one boundary bug is found, audit its sibling read, write, status,
  cleanup, and recovery paths plus every parent component before declaring the
  fix complete.
- Validate protocol numbers by lexical width and numeric range before calling
  `int`; message-size limits alone do not make numeric conversion safe.
- Bound periodic and persistent state as well as request input: journals,
  pending-request maps, retries, queues, and caches must remain bounded for an
  indefinitely long cast.
- Audit the built artifact for dormant entry points, package data, service
  policies, and assets—not only reachable CLI options and imported modules.
- Add adversarial regressions with deadlines. Prove oversized/deep/flooded
  input is bounded, special files cannot block, replacement races cannot
  redirect descriptors, and unrelated targets retain their content and mode.
