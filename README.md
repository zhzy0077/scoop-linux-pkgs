# Scoop Linux main bucket

Canonical repository: **https://github.com/zhzy0077/scoop-linux-pkgs**.

A bucket of native Linux CLI packages for [Scoop on Linux](https://github.com/zhzy0077/scoop-linux). Each manifest under `bucket/` declares its supported architectures, download URLs and SHA-256 checksums.

## Use

[Scoop on Linux](https://github.com/zhzy0077/scoop-linux) uses this repository as its default `main` bucket:

```sh
scoop bucket add main
scoop install main/ripgrep main/jq
```

An explicit Git repository argument, `SCOOP_MAIN_BUCKET` or config `main_bucket_url` can select another source. Built-in bootstrap persists its selected source in `main_bucket_url`; `scoop config rm main_bucket_url` removes that override. Package-manager release artifacts/catalogs are configured separately.

## Automatic package updates

The **Excavator** workflow checks stable GitHub releases every four hours (`20 */4 * * *`). It reads each manifest's `checkver` and `autoupdate` fields, verifies all declared architecture assets and SHA-256 values without executing package binaries, and commits each successful update to `main`. Versions equal to or older than the manifest are skipped. A missing asset or configured checksum-source failure retains that package's existing manifest; other successful packages can publish, and the run reports the failures.

Use **Actions → Excavator → Run workflow** for manual checks. `packages` accepts space/comma-separated package names (empty means all); `dry_run` defaults to true and downloads/verifies candidates without changing manifests or publishing. Publication requires a main-branch invocation. Runs use a fixed concurrency group, a pinned Python Podman image, explicit `contents: write` and normal non-force pushes. Validation runs before pushing; token-authenticated pushes do not normally trigger another workflow.

The run log identifies checksum sources and computed SHA-256 values; the Actions summary lists updated, unchanged and failed packages. No separate provenance ledger is maintained. Branch rules must permit the workflow's `GITHUB_TOKEN` to push routine updates to `main`.

## Maintainer metadata

Version detection supports `checkver: "github"` using the GitHub homepage, or an object with `github` and optional `regex`/`re`. URLs/extraction paths use Scoop substitutions such as `$version`, named regex captures (`$matchTag`), and checksum-source `$baseurl`/`$basename`. The actual upstream tag spelling is preserved. Shared `autoupdate.hash.url` and optional `regex`/`find` configure publisher checksum extraction; a configured source is mandatory. Otherwise GitHub asset digests are used when available, with HTTPS download hashing as fallback. Downloaded bytes are always hashed and compared with the selected authority.

Review upstream notes, licenses, native CPU/libc compatibility and artifact naming when changing templates. Keep all supported targets in the manifest; the updater advances a package only when all are available. Numeric dotted/SemVer versions are compared monotonically. Additional checkver providers require explicit updater support. These fields remain inert authoring metadata to the installed Scoop CLI.

For a local dry run with Python 3.13:

```sh
python .github/update.py --packages jq,ripgrep --dry-run
```

The workflow runs the same updater inside its pinned Linux Podman image. Re-running a completed update is a no-op; unchanged versions are not rehashed. Revert a bot commit to restore the previous manifest. The updater and its generating checks live under `.github/`; package execution and repository-wide integrity gates are not part of this automation.
