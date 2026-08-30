# Marketplace update handoff

Omacast is already published. Its current verified marketplace snapshot is
commit `ca5646f8d36ea7111c788b8408bf99aaa8e694d7`, plugin version 0.1.3.

Do not edit or reopen the closed initial submission to publish a newer commit.
After version 0.1.4 and companion revision 70 pass the scoped exact-candidate
compatibility gates recorded in the canonical roadmap, push that tested commit
and use the marketplace
[Plugin verification form](https://github.com/HANCORE-linux/omarchy-plugin-marketplace/issues/new?template=verify-plugin.yml).

Select **Verify and publish a newer upstream commit** and provide:

- Plugin ID: `hardie.omarchy-cast`
- Repository: `https://github.com/hardiepiennar/omacast`
- Commit: the full 40-character SHA of the pushed, tested release commit

The target SHA must contain the matching manifest version and must already have
a usable companion release. Preserve the old verified snapshot while the
update is pending. If the automated baseline requests capability review, wait
for a marketplace maintainer's exact-commit `approved-and-verified` decision.
