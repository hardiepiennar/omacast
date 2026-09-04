# Marketplace update handoff

Omacast is already published. Its current verified marketplace snapshot is
commit `c5861fcb043b9f90e5854bfddea7934d8445478e`, plugin version 0.1.5.

Do not edit or reopen the closed initial submission to publish a newer commit.
Verification issue
[`#3770`](https://github.com/omacom/omarchy-plugin-marketplace/issues/3770)
is closed and published the current snapshot. For version 0.1.6, use the
marketplace's
[Plugin verification form](https://github.com/omacom/omarchy-plugin-marketplace/issues/new?template=verify-plugin.yml)
after the exact-candidate gates and companion release complete.

Select **Verify and publish a newer upstream commit** and provide:

- Plugin ID: `hardie.omarchy-cast`
- Repository: `https://github.com/hardiepiennar/omacast`
- Commit: the full 40-character SHA of the pushed, tested release commit

The target SHA must contain the matching manifest version and must already have
a usable companion release. Preserve the old verified snapshot while the
update is pending. If the automated baseline requests capability review, wait
for a marketplace maintainer's exact-commit `approved-and-verified` decision.

Keep the verification issue itself terse. Do not add a separate release-summary
comment unless a reviewer asks for one.
