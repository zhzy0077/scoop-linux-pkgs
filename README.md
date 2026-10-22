# Scoop Linux main bucket

Canonical Git repository: **https://github.com/zhzy0077/scoop-linux-pkgs**.

This bucket supplies eleven pinned upstream Linux CLI releases for native `64bit` (x86-64) and `arm64` (aarch64): bat, codex, delta, eza, fd, fzf, jq, just, ripgrep, yq and zoxide. Each manifest declares exact architecture entries, SHA-256 and ordinary executable basenames.

## Use

[Scoop on Linux](https://github.com/zhzy0077/scoop-linux) resolves `main` to this repository by default:

```sh
scoop bucket add main
scoop install main/ripgrep main/jq
```

Install OpenAI's Codex CLI with `scoop install main/codex`. The Linux installer preserves upstream executable basenames, so run `codex-x86_64-unknown-linux-musl` on x86-64 or `codex-aarch64-unknown-linux-musl` on ARM64. For example, append `--help` or `--version` to either command. See the [upstream documentation](https://github.com/openai/codex#readme) for authentication and usage.

An explicit repository argument, `SCOOP_MAIN_BUCKET` or `main_bucket_url` can select an alternate/local Git source. Bootstrap uses the canonical default when environment/config/catalog main-source overrides are absent and persists the selected source in config `main_bucket_url`. Use `scoop config rm main_bucket_url` to remove that override. Release artifact/catalog hosting is configured separately from this bucket.

## Repository layout and publication payload

Publish the **contents** of the package manager's `main/` directory as this repository's root:

```text
README.md
VERIFICATION.json
bucket/
  bat.json       fd.json    jq.json       ripgrep.json
  delta.json     fzf.json   just.json     yq.json
  eza.json       codex.json  zoxide.json
```

Keep `bucket/` directly under the Git root. The payload contains the existing reviewed manifests and provenance records; it contains no release binaries or container-local release catalogs.

From a package-manager source checkout, a local verification repository can be prepared with:

```sh
cp -a main /absolute/linux-main
cd /absolute/linux-main
git init && git add . && git -c user.name=Maintainer -c user.email=maintainer@example.org commit -m 'Linux main'
scoop bucket add main /absolute/linux-main
```

## Maintenance and source trust

Maintenance tooling lives in the [package manager source repository](https://github.com/zhzy0077/scoop-linux):

- [scripts/refresh-main.py](https://github.com/zhzy0077/scoop-linux/blob/main/scripts/refresh-main.py) holds the reviewed version/artifact table and regenerates manifests.
- [tests/verify-main.py](https://github.com/zhzy0077/scoop-linux/blob/main/tests/verify-main.py) requires byte-identical regeneration from a selected artifact cache.
- [tests/release-lifecycle.py](https://github.com/zhzy0077/scoop-linux/blob/main/tests/release-lifecycle.py) validates real native package execution and lifecycle.

Review release notes, licenses, CPU/libc support and artifact layouts when advancing versions. Run maintenance in native Podman tooling containers. Artifact caches are version-isolated. The refresh script downloads both architecture artifacts over HTTPS, records SHA-256 and checks publisher checksum assets for fzf, jq, just, ripgrep and yq. `VERIFICATION.json` records each artifact's source and trust provenance. The selected bat, delta, eza, fd and zoxide releases supply no checksum asset and are explicitly pinned GitHub HTTPS trust-on-first-download. Review available publisher signatures as part of updates.

Codex is maintained directly in this bucket, separately from the package manager's ten-package refresh table. Its musl archives are pinned for both CPUs and their SHA-256 values are checked against GitHub release asset `digest` metadata (`github-release-asset-sha256` in `VERIFICATION.json`). When updating, verify both downloads, archive executable names and native `--version`/`--help` behavior before changing the manifest and provenance together.

Use a fresh cache for independent verification:

```sh
python3 tests/verify-main.py --cache /fresh/artifact-cache
```

Then run the native lifecycle gate on both CPUs and the [validated Fedora/Ubuntu matrix](https://github.com/zhzy0077/scoop-linux/blob/main/README.md#validated-gnu-runtime-matrix). It asserts current links, saved manifests/records, original/restored versions, holds, retained reset, executable behavior and XDG preservation. Its historical/future-held jq metadata versions use the same verified bytes only inside isolated test Git repositories. Published manifests retain upstream versions.

Artifact license identifiers describe the upstream projects; source repositories/archives contain the license texts. GNU artifacts require a compatible glibc. Pinned versions and this reviewed maintenance script are the update mechanism; checkver/autoupdate execution remains outside the MVP.
