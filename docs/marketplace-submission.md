# Omacast marketplace submission draft

Status: **not approved for submission**. Push and verify the public repository,
then have the owner explicitly approve all five statements before checking the
boxes or creating the issue.

Prepared from the [marketplace submission contract](https://github.com/HANCORE-linux/omarchy-plugin-marketplace/blob/55f3491b665e72e72ad12ec8718ee49609db09b6/SUBMISSION.md)
at commit `55f3491b665e72e72ad12ec8718ee49609db09b6` on 2026-08-24.

Issue title: `[Plugin]: Omacast`

```markdown
### Repository URL

https://github.com/hardiepiennar/omacast

### Category

Hardware

### Tags

bar, media, quickshell

### Suggest a missing tag

_No response_

### Maintainer notes

Omacast is a native Omarchy bar plugin for Miracast desktop and audio
mirroring. Its separately installed Arch companion package owns the patched
FluxCast engine and narrowly scoped networking helper. The plugin UI does not
install packages or silently elevate. The current automated baseline is
expected to require review for documented privilege, package-manager, and
service-management capabilities. The external FluxCast
source is pinned to a full immutable commit, and the package-owned Polkit action
permits only the exact guard executable's `prepare` command for active local
users.

### Submission checklist

- [ ] The repository is public and contains installation and removal instructions.
- [ ] I have documented the plugin license and any external dependencies.
- [ ] I confirm that I own or have permission to submit this plugin and its preview assets.
- [ ] The plugin does not overwrite user configuration without explicit consent.
- [ ] I understand that approval is for listing and is not a security review.
```

When every statement is true and explicitly approved by the owner, change all
five boxes to `[x]`, show the final title and body to the owner, and only then
create the issue in `HANCORE-linux/omarchy-plugin-marketplace`. New listings
require the marketplace's current exact-commit baseline plus an explicit
`approved-and-verified` maintainer decision before publication.
