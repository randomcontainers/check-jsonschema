# check-jsonschema

Container images with [check-jsonschema](https://github.com/python-jsonschema/check-jsonschema), which validates JSON, YAML and TOML files against a JSON Schema. It ships schemas for GitHub Actions workflows, GitLab CI, Renovate, Dependabot and other configuration files, and also reads your own schema files and schemas served over HTTP. check-jsonschema is installed from PyPI into a virtual environment on Ubuntu or Alpine. The images are rebuilt when check-jsonschema publishes a release and when the base image changes, for `linux/amd64` and `linux/arm64`.

This is an unofficial build, not affiliated with or endorsed by the check-jsonschema project. Report problems with the image in this repository and problems with check-jsonschema itself in the [check-jsonschema issue tracker](https://github.com/python-jsonschema/check-jsonschema/issues).

## Quick start

Check the GitHub Actions workflows of the repository in the current directory:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
  ghcr.io/randomcontainers/check-jsonschema --builtin-schema vendor.github-workflows .github/workflows/*.yml
```

Your shell expands `.github/workflows/*.yml` to relative paths, which work because the current directory is mounted at the working directory `/work`. check-jsonschema prints `ok -- validation done` and exits with 0 when every file is valid. Otherwise it prints `Schema validation errors were encountered.`, then the file name, the JSON path and the message of each error, and exits with 1. A file that cannot be parsed also gives exit code 1. `-o json` prints the result as JSON instead.

Check the Dependabot and Renovate configuration:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
  ghcr.io/randomcontainers/check-jsonschema --builtin-schema vendor.dependabot .github/dependabot.yml
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
  ghcr.io/randomcontainers/check-jsonschema --builtin-schema vendor.renovate renovate.json
```

Validate data files against a schema of your own:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
  ghcr.io/randomcontainers/check-jsonschema --schemafile schema.json data.yaml
```

## Built-in schemas

`--builtin-schema` takes the name of a schema that check-jsonschema ships, such as `vendor.github-workflows`, `vendor.github-actions` (for `action.yml`), `vendor.dependabot`, `vendor.renovate`, `vendor.gitlab-ci`, `vendor.azure-pipelines`, `vendor.circle-ci`, `vendor.compose-spec` or `vendor.readthedocs`. `docker run --rm ghcr.io/randomcontainers/check-jsonschema --help` lists all of them. Most are copies of [SchemaStore](https://www.schemastore.org/) schemas that check-jsonschema refreshes between releases, so a newer release also has newer schemas. `custom.github-workflows-require-timeout` is check-jsonschema's own schema, which fails any workflow job that does not set `timeout-minutes`.

GitLab CI files that use the `!reference` tag do not parse as plain YAML. Pass `--data-transform gitlab-ci`:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
  ghcr.io/randomcontainers/check-jsonschema --builtin-schema vendor.gitlab-ci --data-transform gitlab-ci .gitlab-ci.yml
```

For Azure Pipelines files with `${{ }}` template expressions, use `--data-transform azure-pipelines`.

## Your own schemas

`--schemafile` takes a path under `/work` or an `http` or `https` URL. Each data file is parsed by its extension: `.json`, `.jsonld` and `.geojson` as JSON, `.yaml`, `.yml`, `.ymlld`, `.eyaml` and `.cff` (as in `CITATION.cff`) as YAML, and `.toml` as TOML. Files with other extensions are read as JSON unless you pass `--default-filetype yaml` or `--force-filetype`. `--check-metaschema schema.json` checks the schema file itself against the metaschema named in its `$schema`.

`pattern` keywords and the `regex` format use ECMAScript regular expression syntax, through the regress library. `--regex-variant python` switches to Python's `re` syntax.

The formats `date`, `date-time`, `time`, `email`, `idn-email`, `idn-hostname`, `ipv4`, `ipv6`, `regex` and `uuid` are checked; `email` and `idn-email` only check that the value contains `@`. Formats that need optional Python libraries, such as `uri`, `hostname` and `duration`, are not: like a plain `pip install check-jsonschema`, the image does not install those libraries. For the same reason it cannot read `.json5` files.

## Network and the cache

Built-in schemas and schema files under `/work` are read locally, so `--network none` works with them, unless the schema has a `$ref` to a URL. A schema given as a URL, and any `$ref` to a URL, is downloaded and cached in `/cache/check_jsonschema/`, which is removed with the container. To reuse downloads between runs, keep `/cache` in a volume:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -v check-jsonschema-cache:/cache \
  ghcr.io/randomcontainers/check-jsonschema --schemafile https://example.com/schema.json config.yaml
```

check-jsonschema still contacts the server on every run, and downloads the file again only when the server reports a newer `Last-Modified` time. `--no-cache` skips the cache.

Downloads go through requests, which verifies HTTPS against the CA bundle of the Python package certifi and ignores the system certificates. Behind a proxy that re-signs HTTPS with a company CA, add that CA to the system store in an image built on `slim` and set `REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt`, or mount a PEM file with the CA and pass `-e REQUESTS_CA_BUNDLE=<path in the container>`.

## What is in the image

- `check-jsonschema` in `/usr/local/bin`, linked from a virtual environment in `/usr/local/lib/check-jsonschema` that uses the distro's `python3`.
- Its dependencies, installed from `requirements.lock`: jsonschema and referencing, which validate the files and resolve `$ref`, regress for ECMAScript regular expressions, ruamel.yaml for YAML, click and requests.
- pip, which stays in the virtual environment.
- On Alpine, the `libgcc_s` that the musl wheels of rpds-py and regress carry in `rpds_py.libs` and `regress.libs`. On Ubuntu they use the distro's `libgcc-s1`.

## Default or slim

check-jsonschema's default image adds no other tools, so `latest` and `slim` are the same image, with the contents listed above. Use `latest` to run it and the `slim` tags as a base for your own image.

`ghcr.io/randomcontainers/check-jsonschema-yamllint`, built in the [check-jsonschema-yamllint](https://github.com/randomcontainers/check-jsonschema-yamllint) repository, adds [yamllint](https://github.com/randomcontainers/yamllint), so one image can check YAML files for syntax and formatting problems and validate them against a schema. Its entrypoint is `check-jsonschema`; run yamllint with `--entrypoint yamllint`:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" --entrypoint yamllint \
  ghcr.io/randomcontainers/check-jsonschema-yamllint .github/workflows
```

## Tags

`<version>` is a check-jsonschema release such as `0.38.2`. `<minor>` and `<major>` are its shorter forms, `0.38` and `0`, and follow the newest release in that series. Each row lists the default tag and its `slim` twin, which point to the same image.

| Tags | Base |
|---|---|
| `latest`, `slim` | Ubuntu |
| `<version>`, `<version>-slim` | Ubuntu |
| `<minor>`, `<minor>-slim`, `<major>`, `<major>-slim` | Ubuntu |
| `ubuntu`, `slim-ubuntu` | Ubuntu |
| `<version>-ubuntu`, `<version>-slim-ubuntu` | Ubuntu |
| `<minor>-ubuntu`, `<minor>-slim-ubuntu`, `<major>-ubuntu`, `<major>-slim-ubuntu` | Ubuntu |
| `<version>-ubuntu26.04`, `<version>-slim-ubuntu26.04` | Ubuntu 26.04 |
| `alpine`, `slim-alpine` | Alpine |
| `<version>-alpine`, `<version>-slim-alpine` | Alpine |
| `<minor>-alpine`, `<minor>-slim-alpine`, `<major>-alpine`, `<major>-slim-alpine` | Alpine |
| `<version>-alpine3.24`, `<version>-slim-alpine3.24` | Alpine 3.24 |

check-jsonschema has no 1.0 release yet, and a new `0.x` minor release can change options or behavior. The `0` tags follow every release, so in CI pin `<version>` or `<minor>` (`0.38`) instead.

The images are currently built on Ubuntu 26.04 and Alpine 3.24. Tags without a distro version move to the next distro release when the project does; tags ending in `ubuntu26.04` or `alpine3.24` stay on that release and are no longer rebuilt once the project moves to the next one. Every tag of the current check-jsonschema version, including the exact version, is rebuilt in place (see [Updates](#updates)), so pin a digest when you need the same bytes every time.

## Platforms

`linux/amd64` and `linux/arm64`, for both Ubuntu and Alpine. Both are built natively on GitHub-hosted runners, without emulation. The same `requirements.lock` is installed on all four combinations of distro and architecture. check-jsonschema and most of its dependencies are pure Python; rpds-py, regress and charset-normalizer come as glibc (`manylinux`) wheels on Ubuntu and musl (`musllinux`) wheels on Alpine.

## Files and permissions

The working directory is `/work`. The image runs as UID 1000, and any other UID works too: `HOME` is then `/`, and caches go to `/cache`, which anyone can write to. check-jsonschema only reads the files it checks, so the mount can be read-only (`-v "$PWD:/work:ro"`). It can read only the files its UID has access to, so run it as your own user:

| Runtime | Flag |
|---|---|
| Docker on Linux (rootful), GitHub Actions | `--user "$(id -u):$(id -g)"` |
| Rootless Podman | `--userns=keep-id` |
| Rootless Docker | `--user 0:0` (root in the container is your user on the host) |
| Docker Desktop on macOS or Windows | none, file ownership is mapped for you |

## Extending the slim image

Use a `slim` tag as the base for your own image. `slim`, `slim-ubuntu` and `slim-alpine` follow new check-jsonschema releases and are rebuilt when its locked Python dependencies or the distro change. check-jsonschema runs from a virtual environment in `/usr/local/lib/check-jsonschema`; the `python3` on `PATH` is the distro's interpreter and cannot import check-jsonschema. The distro packages check-jsonschema needs are listed in `/usr/local/share/randomcontainers/check-jsonschema/runtime-deps`. Switch to root to install more, then back:

```dockerfile
FROM ghcr.io/randomcontainers/check-jsonschema:slim-ubuntu@sha256:...
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
USER 1000:1000
```

On Alpine, use `apk add --no-cache git`. The entrypoint is `["tini", "--", "check-jsonschema"]`; set your own `ENTRYPOINT` if your image runs a script. To pick up new check-jsonschema releases and base image fixes, let Dependabot or Renovate update the digest in your `FROM` line.

## Verifying

Each image has a build provenance attestation from this repository's GitHub Actions run, signed by the shared build workflow in `randomcontainers/ci`:

```sh
gh attestation verify oci://ghcr.io/randomcontainers/check-jsonschema:latest \
  --repo randomcontainers/check-jsonschema --signer-repo randomcontainers/ci
```

Each platform image also carries an SPDX SBOM that lists the distro and Python packages in it with their versions:

```sh
docker buildx imagetools inspect ghcr.io/randomcontainers/check-jsonschema:latest --format '{{ json .SBOM }}'
```

check-jsonschema and its Python dependencies are installed from [`requirements.lock`](requirements.lock), which pins every package to a version and its SHA-256 hashes, and pip refuses anything else. The URL and hash of each installed wheel are in `/usr/local/share/randomcontainers/check-jsonschema/source`, and the installed versions in `buildinfo` next to it.

## Updates

The project checks [check-jsonschema on PyPI](https://pypi.org/project/check-jsonschema/) every 15 minutes. A release is picked up once it is 24 hours old and PyPI holds a provenance attestation showing it was published from the [python-jsonschema/check-jsonschema](https://github.com/python-jsonschema/check-jsonschema) repository. The new version and a regenerated `requirements.lock` are then committed together and the images are rebuilt. Only the newest release is built; tags of older versions stay as they were last built. Dependency versions in the lock must have been on PyPI for at least 7 days. The lock is also regenerated weekly at the same check-jsonschema version, so new releases of dependencies such as jsonschema, requests and certifi reach the current tags between check-jsonschema releases. When a new lock would change the rpds-py or regress version, the update, including a new check-jsonschema release, waits until the license files of their Rust crates are refreshed by hand (see [Building](#building)). A release that adds, removes or changes a license file of the built-in schemas is not published until the license in `package.yml` and `schema-licenses.sha256` have been reviewed and updated.

The images of the current version are also rebuilt when the Ubuntu or Alpine base image changes and at least every 7 days, so distro security fixes reach the current tags.

## Building

```sh
docker build -f Dockerfile.ubuntu --target slim --build-arg VERSION=<version> -t check-jsonschema:local .
```

For the Alpine image, use `Dockerfile.alpine` and also pass the three `extra-artifacts` entries of `package.yml` as build arguments: `GCC_VERSION` is the `version` of `gcc`, and `GCC_URL`, `GCC_SHA256`, `MUSL_CROSS_MAKE_URL`, `MUSL_CROSS_MAKE_SHA256`, `RUST_MUSL_CROSS_URL` and `RUST_MUSL_CROSS_SHA256` are the `url`, with `{version}` filled in, and the `sha256` of each entry. `<version>` must be the version pinned in `requirements.lock`; the build stops otherwise. It also stops when the license files of the built-in schemas differ in name or content from [`schema-licenses.sha256`](schema-licenses.sha256), and when a wheel carries a shared library other than the libgcc_s described under [Licenses](#licenses), which [`scripts/check-libraries.py`](scripts/check-libraries.py) checks.

The command that produced `requirements.lock` is on its second line; it resolves for Python 3.14, the version on both bases. To regenerate the lock at the version in `package.yml`, run `rc lock requirements.lock` in this directory. `rc` is the command-line tool of [randomcontainers/ci](https://github.com/randomcontainers/ci), and it needs uv on `PATH`. If a new lock changes the rpds-py or regress version, run `python3 scripts/bundled-licenses.py` (Python 3.11 or later) to refresh `licenses/`. The build fails until those files match the installed packages. The script stops when a new musllinux wheel carries a libgcc_s that `TOOLCHAIN` in `scripts/check-libraries.py` does not list, or one from another GCC release than the `gcc` entry in `package.yml`. Find the image that built the wheel in the wheel project's CI log, and update `TOOLCHAIN` and the three source entries before running it again.

## Licenses

check-jsonschema is licensed under the [Apache License 2.0](https://github.com/python-jsonschema/check-jsonschema/blob/main/LICENSE) (Apache-2.0). It is pure Python, so the installed files are its source, and the wheel it was installed from is named in `/usr/local/share/randomcontainers/check-jsonschema/source`. The image adds its Python dependencies, which are mostly under the MIT license, plus requests (Apache-2.0), click and idna (BSD-3-Clause) and certifi (MPL-2.0), and pip (MIT), which includes packages under the Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, MIT, MPL-2.0 and PSF-2.0 licenses. The complete SPDX expression is in the image's `org.opencontainers.image.licenses` label, and each package's license files are in `/usr/local/share/randomcontainers/check-jsonschema/licenses/`.

The built-in schemas are JSON files that check-jsonschema reads as data. Most come from SchemaStore (Apache-2.0), and others from the projects they describe, mostly under the MIT license. Three have other terms: the Renovate schema (AGPL-3.0-only), the Snapcraft schema (GPL-3.0-only) and the Citation File Format schema (CC-BY-4.0). The label lists those three licenses because of these files only. The license files of all built-in schemas, and check-jsonschema's notes on which schema comes from where, are in `/usr/local/share/randomcontainers/check-jsonschema/licenses/check-jsonschema/vendor-schemas/`. [`schema-licenses.sha256`](schema-licenses.sha256) lists those files with their SHA-256 checksums, and the build stops when a check-jsonschema release adds, removes or changes one of them, so the label is reviewed before such a release is published.

rpds-py and regress are Rust extension modules. Their wheels link static builds of Rust crates such as PyO3, rpds and the regress crate, and of the Rust standard library with the crates it uses, but include only their own license. Each of those crates is licensed under MIT or offers it as a choice, except compiler-builtins in the standard library, which is under MIT AND Apache-2.0 WITH LLVM-exception. This repository adds the license files of the crates and the standard library in [`licenses/`](licenses/), and the image has them in `licenses/rpds-py/bundled/` and `licenses/regress/bundled/` under `/usr/local/share/randomcontainers/check-jsonschema/`. The `SOURCES` file in each names the source release, the standard library's `rust-src` archive and every crate archive, with the checksums they were checked against.

The musllinux wheels, which the Alpine image installs, also include a copy of libgcc_s, the GCC runtime library that the Rust code uses for unwinding. It is licensed under GPL-3.0-or-later with the GCC Runtime Library Exception 3.1, and both texts are in the same `bundled/` directories. The copies come from the `ghcr.io/rust-cross/rust-musl-cross` images that built the wheels, where GCC 12.4.0 is compiled by musl-cross-make, which applies its own patches to GCC, with the build configuration of rust-musl-cross. Every release of this repository, named `v<version>` after the check-jsonschema version, has the corresponding source attached: `gcc-12.4.0.tar.xz` with its signature, and the musl-cross-make and rust-musl-cross repositories at the commits that built the library, as `<commit>.tar.gz`. All three are pinned under `extra-artifacts` in `package.yml`, and `SOURCES` explains how the library was matched to them. In the Alpine image, the last lines of `/usr/local/share/randomcontainers/check-jsonschema/source` name the three archives and the release.

The files in this repository are available under the MIT license, see [LICENSE](LICENSE).

## Requesting a tool

To suggest another tool, use the [Request a tool](https://github.com/randomcontainers/.github/issues/new?template=tool-request.yml) form.
