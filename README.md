# Scoop Linux main bucket

Canonical repository: **https://github.com/zhzy0077/scoop-linux-pkgs**.

Eleven pinned upstream Linux CLI packages are available for native `64bit` (x86-64) and `arm64` (aarch64): bat, delta, eza, fd, fzf, jq, just, pi, ripgrep, yq and zoxide. Download URLs and SHA-256 checksums are stored in each package manifest under `bucket/`.

## Use

[Scoop on Linux](https://github.com/zhzy0077/scoop-linux) uses this repository as its default `main` bucket:

```sh
scoop bucket add main
scoop install main/ripgrep main/jq
scoop install main/pi
```

Supply an explicit Git repository argument to use another source. `SCOOP_MAIN_BUCKET` and config `main_bucket_url` also override the default. Built-in bootstrap persists its selected source in `main_bucket_url`; `scoop config rm main_bucket_url` removes that override.

## Maintainer updates

Edit the corresponding `bucket/<package>.json` when updating a package:

1. Review upstream release notes, license terms and native Linux CPU/libc requirements.
2. Update the version and the exact `64bit`/`arm64` artifact URLs. Compute each artifact's SHA-256 and compare publisher checksums or signatures when available.
3. Check archive layout, extraction fields and declared executable paths. Preserve ordinary executable basenames.
4. Validate installation and execution on the supported native Fedora/Ubuntu systems before committing the manifest update.

The pi coding agent uses standalone upstream archives (no Node.js or Bun installation required), retaining the executable and companion resources under `extract_dir: "pi"`. Both Linux artifacts are verified against the release's [`SHA256SUMS`](https://github.com/earendil-works/pi/releases/download/v1.4.0/SHA256SUMS).

Artifact license identifiers describe the upstream projects; their source repositories and archives provide the license texts. Package data remains in `bucket/` directly under this Git root. Release binaries and catalogs for the package manager are configured separately.
