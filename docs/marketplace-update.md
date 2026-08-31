# Marketplace update handoff

Omacast is already published. Its current verified marketplace snapshot is
commit `ca5646f8d36ea7111c788b8408bf99aaa8e694d7`, plugin version 0.1.3.

Do not edit or reopen the closed initial submission to publish a newer commit.
Verification issue
[`#3770`](https://github.com/omacom/omarchy-plugin-marketplace/issues/3770)
currently targets the superseded version-0.1.4 commit. Before review, retarget
that open issue to the full version-0.1.5 release SHA after its exact-candidate
gates and companion release complete. If that issue closes first, use the
marketplace
[Plugin verification form](https://github.com/omacom/omarchy-plugin-marketplace/issues/new?template=verify-plugin.yml)
instead of reopening it.

Select **Verify and publish a newer upstream commit** and provide:

- Plugin ID: `hardie.omarchy-cast`
- Repository: `https://github.com/hardiepiennar/omacast`
- Commit: the full 40-character SHA of the pushed, tested release commit

The target SHA must contain the matching manifest version and must already have
a usable companion release. Preserve the old verified snapshot while the
update is pending. If the automated baseline requests capability review, wait
for a marketplace maintainer's exact-commit `approved-and-verified` decision.

Add one short human comment after the target SHA is final. Summarize the
user-visible and security-boundary changes, link the project issues resolved by
the release, name the exact commit, and ask for review. Do not include a test
recital or promotional release prose.
