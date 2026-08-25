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
