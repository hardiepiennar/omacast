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
- Preserve conflicting evidence in the research log; mark conclusions
  superseded instead of rewriting history.
- Update the canonical plan and acceptance evidence as decisions change.
- Keep marketplace and review comments terse and human: state the relevant
  change, exact commit, and requested action. Do not add test-result recitals,
  promotional detail, or release-summary prose unless the reviewer requires it.
- After pushing marketplace review fixes, update the existing submission issue
  body to name the full current remote HEAD while preserving its required
  headings and checked checklist. Editing the issue reruns commit-bound
  validation; posting a review comment alone does not.

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
- Add adversarial regressions with deadlines. Prove oversized/deep/flooded
  input is bounded, special files cannot block, replacement races cannot
  redirect descriptors, and unrelated targets retain their content and mode.
