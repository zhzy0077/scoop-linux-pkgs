#!/usr/bin/env python3
"""Generate Linux Scoop updates from manifest-owned GitHub release metadata.

Only authoring metadata is evaluated. Downloaded package bytes are hashed, never
executed or extracted. A package is written only after every declared target passes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]*\Z")
SHA256 = re.compile(r"[A-Fa-f0-9]{64}\Z")
VERSION = re.compile(r"(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?\Z")
VARIABLE = re.compile(r"\$([A-Za-z][A-Za-z0-9]*|\d+)")
FIELDS = {"url", "hash", "extract_dir", "extract_to", "bin"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None or value in ("", "null"):
        return False
    if isinstance(value, str):
        if value in ('"true"', '"false"'):
            value = json.loads(value)
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
    raise ValueError("dry-run must be true or false")


def https(url):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not any(ord(c) < 32 for c in url),
            f"HTTPS URL required: {url!r}")
    return parsed


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, request, fp, code, message, headers, newurl):
        parsed = https(newurl)
        result = super().redirect_request(request, fp, code, message, headers, newurl)
        if result is not None and parsed.hostname != "api.github.com":
            result.remove_header("Authorization")
        return result


class Client:
    def __init__(self, token=None):
        self.token = token
        self.opener = urllib.request.build_opener(SecureRedirect())
        self.text_cache = {}
        self.digest_cache = {}

    def open(self, url):
        parsed = https(url)
        headers = {"User-Agent": "scoop-linux-pkgs-excavator", "Accept": "application/octet-stream"}
        if parsed.hostname == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
        try:
            return self.opener.open(urllib.request.Request(url, headers=headers), timeout=120)
        except (urllib.error.URLError, TimeoutError) as error:
            raise ValueError(f"fetch failed for {url}: {error}") from error

    def text(self, url):
        if url not in self.text_cache:
            with self.open(url) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            require(len(data) <= 16 * 1024 * 1024, f"metadata exceeds 16 MiB: {url}")
            self.text_cache[url] = data.decode("utf-8-sig")
        return self.text_cache[url]

    def release(self, repository):
        return json.loads(self.text(f"https://api.github.com/repos/{repository}/releases/latest"))

    def digest(self, url):
        if url not in self.digest_cache:
            digest = hashlib.sha256()
            length = 0
            try:
                with self.open(url) as response:
                    while chunk := response.read(1024 * 1024):
                        length += len(chunk)
                        require(length <= 1024 * 1024 * 1024, f"asset exceeds 1 GiB: {url}")
                        digest.update(chunk)
            except Exception as error:
                raise ValueError(f"asset download/hash failed for {url}: {error}") from error
            self.digest_cache[url] = digest.hexdigest()
        return self.digest_cache[url]


def repository(checkver, homepage):
    if checkver == "github":
        url, regex = homepage, r"^[vV]?(\d+(?:\.\d+)*)$"
    else:
        require(isinstance(checkver, dict) and set(checkver) <= {"github", "regex", "re"},
                "checkver supports github with optional regex/re only")
        url = checkver.get("github")
        regex = checkver.get("regex", checkver.get("re", r"^[vV]?(\d+(?:\.\d+)*)$"))
    require(isinstance(url, str) and isinstance(regex, str), "invalid GitHub checkver")
    parsed = https(url)
    require(parsed.hostname == "github.com" and not parsed.query and not parsed.fragment,
            "checkver.github must identify a GitHub repository")
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    require(len(parts) == 2 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts),
            f"invalid GitHub repository: {url}")
    return "/".join(parts), regex


def capture_version(tag, pattern):
    # Scoop uses .NET named groups and numbers unnamed groups before named ones.
    pattern = re.sub(r"\(\?<([A-Za-z][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)
    match = re.search(pattern, tag)
    require(match is not None, f"tag {tag!r} does not match checkver regex")
    named = set(match.re.groupindex.values())
    order = [i for i in range(1, match.re.groups + 1) if i not in named]
    order += sorted(named)
    require(order, "checkver regex must capture a version")
    version = match.group(order[0])
    require(isinstance(version, str) and VERSION.fullmatch(version),
            f"unsupported extracted version: {version!r}")
    substitutions = {"version": version}
    for number, group in enumerate(order, 1):
        substitutions[f"match{number}"] = match.group(group) or ""
    for name, value in match.groupdict().items():
        substitutions["match" + name[:1].upper() + name[1:]] = value or ""
    return version, substitutions


def compare_versions(left, right):
    a, b = VERSION.fullmatch(left), VERSION.fullmatch(right)
    require(a is not None and b is not None, "versions must use numeric dotted/SemVer form")
    x, y = [int(p) for p in a[1].split(".")], [int(p) for p in b[1].split(".")]
    size = max(len(x), len(y))
    x += [0] * (size - len(x)); y += [0] * (size - len(y))
    if x != y:
        return (x > y) - (x < y)
    if a[2] is None or b[2] is None:
        return (a[2] is None) - (b[2] is None)
    x, y = a[2].split("."), b[2].split(".")
    for lpart, rpart in zip(x, y):
        if lpart == rpart:
            continue
        if lpart.isdigit() and rpart.isdigit():
            if int(lpart) == int(rpart):
                continue
            return (int(lpart) > int(rpart)) - (int(lpart) < int(rpart))
        if lpart.isdigit() != rpart.isdigit():
            return -1 if lpart.isdigit() else 1
        return (lpart > rpart) - (lpart < rpart)
    return (len(x) > len(y)) - (len(x) < len(y))


def render(value, substitutions, *, url=False, regex=False):
    if isinstance(value, list):
        return [render(item, substitutions, url=url, regex=regex) for item in value]
    require(isinstance(value, str), "template must be a string or string array")

    def replacement(match):
        key = match[1]
        if regex and key == "sha256":
            return r"([a-fA-F0-9]{64})"
        require(key in substitutions, f"unknown template variable: ${key}")
        text = substitutions[key]
        if regex:
            return re.escape(text)
        if url and key not in ("baseurl", "url"):
            return urllib.parse.quote(text, safe=".-_~")
        return text
    return VARIABLE.sub(replacement, value)


def relative(value, label):
    values = value if isinstance(value, list) else [value]
    require(values, f"empty {label}")
    for item in values:
        require(isinstance(item, str) and item and not PurePosixPath(item).is_absolute()
                and all(part not in ("", ".", "..") for part in item.split("/")) and "\\" not in item
                and not any(ord(c) < 32 for c in item), f"unsafe {label}: {item!r}")


def validate_manifest(document):
    require(isinstance(document, dict) and isinstance(document.get("version"), str)
            and VERSION.fullmatch(document["version"]), "invalid manifest version")
    require(isinstance(document.get("homepage"), str) and document["homepage"], "homepage required")
    license_value = document.get("license")
    require(isinstance(license_value, str) and license_value or isinstance(license_value, dict)
            and isinstance(license_value.get("identifier"), str) and license_value["identifier"],
            "license identifier required")
    architectures = document.get("architecture")
    require(architectures is None or isinstance(architectures, dict) and architectures,
            "architecture must be a nonempty object")
    if architectures is not None:
        require(all(re.fullmatch(r"(?:64bit|32bit|arm64|arm|[a-z][a-z0-9_]*)", name)
                    for name in architectures), "invalid architecture name")
    slots = architectures.values() if architectures is not None else [document]
    for slot in slots:
        require(isinstance(slot, dict), "architecture entry must be an object")
        urls = slot.get("url", document.get("url"))
        hashes = slot.get("hash", document.get("hash"))
        urls = urls if isinstance(urls, list) else [urls]
        hashes = hashes if isinstance(hashes, list) else [hashes]
        require(urls and len(urls) == len(hashes), "URL/hash arrays must be paired")
        for url, digest in zip(urls, hashes):
            require(isinstance(url, str), "artifact URL required")
            https(url)
            require(isinstance(digest, str) and SHA256.fullmatch(digest.removeprefix("sha256:")),
                    "SHA-256 required for each artifact")
        for field in ("extract_dir", "extract_to", "bin"):
            if field in slot:
                relative(slot[field], field)
        binaries = slot.get("bin", document.get("bin"))
        if binaries is not None:
            relative(binaries, "bin")
            binaries = binaries if isinstance(binaries, list) else [binaries]
            names = [PurePosixPath(value).name for value in binaries]
            require(len(names) == len(set(names)) and "scoop" not in names,
                    "duplicate or reserved executable basename")
    for field in ("extract_dir", "extract_to", "bin"):
        if field in document:
            relative(document[field], field)


def exact_asset(url, release, repo):
    parsed = https(url)
    parts = parsed.path.split("/")
    require(parsed.hostname == "github.com" and not parsed.query and len(parts) == 7
            and "/".join(parts[1:3]).lower() == repo.lower()
            and parts[3:5] == ["releases", "download"], f"not a release asset URL: {url}")
    require(urllib.parse.unquote(parts[5]) == release["tag_name"],
            f"rendered tag differs from actual tag {release['tag_name']!r}: {url}")
    name = urllib.parse.unquote(parts[6])
    require(name and name not in (".", "..") and not any(c in name for c in "/\\\x00\r\n"),
            f"invalid release asset name: {name!r}")
    matches = [asset for asset in release["assets"] if asset.get("name") == name]
    require(len(matches) == 1, f"expected one exact release asset {name!r}; found {len(matches)}")
    asset = matches[0]
    actual = https(asset["browser_download_url"])
    require(actual.hostname == "github.com" and not actual.query and not actual.fragment,
            "invalid GitHub asset download location")
    require([urllib.parse.unquote(p) for p in actual.path.split("/")]
            == [urllib.parse.unquote(p) for p in parts], "asset URL differs from rendered target")
    if parsed.fragment:
        require(parsed.fragment.startswith("/") and parsed.fragment.count("/") == 1,
                "unsupported artifact filename fragment")
        relative(parsed.fragment[1:], "artifact filename")
    # Keep the API's actual tag spelling/encoding and the manifest's filename alias.
    return asset, urllib.parse.urlunsplit(actual._replace(fragment=parsed.fragment))


def publisher_hash(config, substitutions, client):
    require(isinstance(config, dict) and set(config) <= {"url", "regex", "find", "mode"},
            "hash source supports url and optional regex/find/mode")
    require(config.get("mode", "extract") == "extract", "only text hash extraction is supported")
    source = render(config["url"], substitutions, url=True)
    try:
        text = client.text(source)
    except Exception as error:
        raise ValueError(f"configured checksum source failed for {source}: {error}") from error
    pattern = config.get("regex", config.get("find"))
    if pattern is not None:
        pattern = render(pattern, substitutions, regex=True)
        matches = list(re.finditer(pattern, text, re.MULTILINE))
        require(matches and all(m.lastindex for m in matches), f"checksum regex did not match: {source}")
        digests = {m.group(1).lower() for m in matches}
    else:
        name = substitutions["basename"]
        lines = text.splitlines()
        single = text.strip()
        if SHA256.fullmatch(single):
            digests = {single.lower()}
        else:
            digests = set()
            for line in lines:
                tokens = line.split()
                if name in [token.lstrip("*").removeprefix("./") for token in tokens]:
                    digests.update(token.lower() for token in tokens if SHA256.fullmatch(token))
    require(digests and all(SHA256.fullmatch(d) for d in digests),
            f"no SHA-256 for {substitutions['basename']} in {source}")
    return digests, source


def build_update(document, client):
    validate_manifest(document)
    repo, pattern = repository(document["checkver"], document["homepage"])
    release = client.release(repo)
    require(isinstance(release, dict) and isinstance(release.get("draft"), bool)
            and isinstance(release.get("prerelease"), bool), "malformed GitHub release")
    if release["draft"] or release["prerelease"]:
        return None, "draft/prerelease skipped", []
    tag = release.get("tag_name")
    require(isinstance(tag, str) and tag and len(tag) < 256
            and not any(ord(c) < 32 for c in tag), "invalid GitHub tag")
    version, substitutions = capture_version(tag, pattern)
    if compare_versions(version, document["version"]) <= 0:
        return None, f"{version} is not newer than {document['version']}", []
    require(isinstance(release.get("assets"), list), "release assets missing")
    auto = document["autoupdate"]
    require(isinstance(auto, dict) and set(auto) <= FIELDS | {"architecture"},
            "unsupported autoupdate fields")
    auto_arch = auto.get("architecture", {})
    require(isinstance(auto_arch, dict), "autoupdate.architecture must be an object")
    result = copy.deepcopy(document)
    sources = []
    plans = []
    architectures = document.get("architecture")
    names = list(architectures) if architectures is not None else [None]
    # Existing shared URLs are updated as well as explicit native target entries.
    if architectures is not None and "url" in document:
        names.append(None)
    for arch in names:
        specific = auto_arch.get(arch, {}) if arch is not None else {}
        require(isinstance(specific, dict) and set(specific) <= FIELDS - {"extract_to"},
                f"unsupported autoupdate architecture fields: {arch}")
        template = specific.get("url", auto.get("url"))
        urls = render(template, substitutions, url=True)
        urls = urls if isinstance(urls, list) else [urls]
        hash_config = specific.get("hash", auto.get("hash"))
        if isinstance(hash_config, list):
            require(len(hash_config) == len(urls), "hash source array must match URLs")
            configs = hash_config
        else:
            configs = [hash_config] * len(urls)
        assets = [exact_asset(url, release, repo) for url in urls]
        plans.append((arch, specific, isinstance(template, list), assets, configs))
    # Every declared asset is resolved before network hashing or any manifest write.
    for field in ("extract_dir", "extract_to", "bin"):
        if field in auto:
            result[field] = render(auto[field], substitutions)
    for arch, specific, array, assets, configs in plans:
        slot = result if arch is None else result["architecture"][arch]
        urls, hashes = [], []
        for (asset, url), config in zip(assets, configs):
            remote = urllib.parse.urldefrag(url)[0]
            parsed = urllib.parse.urlsplit(remote)
            variables = dict(substitutions, baseurl=remote.rsplit("/", 1)[0],
                             basename=asset["name"], url=remote)
            expected = None
            if config is not None:
                expected, authority = publisher_hash(config, variables, client)
                method = f"publisher {authority}"
            elif asset.get("digest"):
                digest = asset["digest"]
                require(isinstance(digest, str) and digest.startswith("sha256:")
                        and SHA256.fullmatch(digest[7:]), "malformed GitHub asset SHA-256")
                expected, method = {digest[7:].lower()}, "GitHub asset digest"
            else:
                method = "downloaded HTTPS asset"
            actual = client.digest(remote)
            require(SHA256.fullmatch(actual), f"invalid computed SHA-256: {remote}")
            require(expected is None or actual in expected, f"SHA-256 mismatch against {method}: {remote}")
            urls.append(url); hashes.append(actual)
            sources.append(f"{arch or 'independent'}: {remote} SHA256={actual} source={method}; downloaded bytes verified")
        slot["url"] = urls if array else urls[0]
        slot["hash"] = hashes if array else hashes[0]
        for field in ("extract_dir", "bin"):
            if field in specific:
                slot[field] = render(specific[field], substitutions)
            elif arch is not None and field in auto and field in slot:
                slot[field] = render(auto[field], substitutions)
    result["version"] = version
    require(set(result.get("architecture", {})) == set(document.get("architecture", {})),
            "architecture set changed")
    validate_manifest(result)
    return result, f"{document['version']} -> {version}; actual tag {tag}", sources


def atomic_write(path, original, content):
    require(path.read_bytes() == original, "manifest changed while update was being generated")
    mode = stat.S_IMODE(path.stat().st_mode)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}-", delete=False) as file:
            name = file.name
            os.fchmod(file.fileno(), mode)
            file.write(content); file.flush(); os.fsync(file.fileno())
        require(path.read_bytes() == original, "manifest changed before atomic replacement")
        os.replace(name, path)
        name = None
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)


def process(path, dry_run, client):
    row = {"name": path.stem, "path": str(path), "status": "failed", "old_sha256": None}
    try:
        require(NAME.fullmatch(path.stem) and not path.is_symlink(), "unsafe manifest path")
        original = path.read_bytes()
        row["old_sha256"] = hashlib.sha256(original).hexdigest()
        document = json.loads(original.decode("utf-8-sig"))
        if "checkver" not in document or "autoupdate" not in document:
            row.update(status="unchanged", message="no update metadata")
            return row
        result, message, sources = build_update(document, client)
        row["message"] = message
        if result is None:
            row["status"] = "unchanged"
            return row
        content = (json.dumps(result, indent=4, ensure_ascii=False) + "\n").encode()
        require(json.loads(content) == result, "generated JSON round trip failed")
        row.update(version=result["version"], architectures=list(result.get("architecture", {})),
                   new_sha256=hashlib.sha256(content).hexdigest(), sources=sources)
        for source in sources:
            print(f"{path.stem}: {source}", flush=True)
        if not dry_run:
            atomic_write(path, original, content)
        row["status"] = "would-update" if dry_run else "updated"
    except Exception as error:
        row["message"] = f"{type(error).__name__}: {error}".replace("\n", " ").replace("\r", " ")
    return row


def select(bucket, packages):
    require(bucket.is_dir() and not bucket.is_symlink(), f"bucket directory missing/unsafe: {bucket}")
    names = list(dict.fromkeys(filter(None, re.split(r"[,\s]+", packages))))
    if not names:
        return sorted(bucket.glob("*.json")), []
    paths, errors = [], []
    for name in names:
        if not NAME.fullmatch(name):
            errors.append({"name": name, "status": "failed", "message": "invalid package selector"})
        elif not (bucket / f"{name}.json").is_file():
            errors.append({"name": name, "status": "failed", "message": "package not found"})
        else:
            paths.append(bucket / f"{name}.json")
    return paths, errors


def save(path, text):
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def validate_results(report):
    for row in report["packages"]:
        if "path" not in row or row.get("old_sha256") is None:
            continue
        path = Path(row["path"])
        require(path == Path("bucket") / f"{row['name']}.json", "unexpected result path")
        expected = row["new_sha256"] if row["status"] == "updated" else row["old_sha256"]
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                f"result bytes changed unexpectedly: {path}")
        if row["status"] == "updated":
            document = json.loads(path.read_bytes())
            validate_manifest(document)
            require(document["version"] == row["version"] and
                    list(document.get("architecture", {})) == row["architectures"],
                    f"generated version/architecture mismatch: {path}")
    print("All generated results validated before publication.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", type=Path, default=Path("bucket"))
    parser.add_argument("--packages", default=os.environ.get("PACKAGES", ""))
    parser.add_argument("--dry-run", action="store_true", default=parse_bool(os.environ.get("DRY_RUN", "false")))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--commits", type=Path)
    parser.add_argument("--validate-results", type=Path)
    args = parser.parse_args(argv)
    if args.validate_results:
        validate_results(json.loads(args.validate_results.read_text()))
        return 0
    client = Client(os.environ.get("GITHUB_TOKEN"))
    paths, rows = select(args.bucket, args.packages)
    rows += [process(path, args.dry_run, client) for path in paths]
    report = {"dry_run": args.dry_run, "packages": rows}
    summary = ["## Excavator" + (" (dry run)" if args.dry_run else ""), "", "| Package | Result | Detail |", "| --- | --- | --- |"]
    for row in rows:
        print(f"{json.dumps(row['name'])}: {row['status']}: {row.get('message', '')}", flush=True)
        detail = html.escape(row.get("message", "")).replace("|", "&#124;")
        summary.append(f"| {html.escape(row['name']).replace('|', '&#124;')} | {row['status']} | {detail} |")
    save(args.report, json.dumps(report, indent=2) + "\n")
    save(args.summary, "\n".join(summary) + "\n")
    save(args.commits, "".join(f"{row['path']}\t{row['name']}: Update to version {row['version']}\n"
                              for row in rows if row["status"] == "updated"))
    return 1 if any(row["status"] == "failed" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
