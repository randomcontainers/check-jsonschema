"""Collect the license files of code that rpds-py and regress bundle without them.

Usage: bundled-licenses.py

rpds-py and regress are Rust extension modules built with PyO3. Their Linux
wheels link a static build of every Rust crate the extension depends on, but
include only the package's own license. For each of them this reads the
version from requirements.lock, downloads the source release from PyPI and
checks its SHA-256, and walks the Cargo.lock in it from the extension crate
along the dependencies that are linked on Linux. Build dependencies and
procedural macros run only while building and are skipped. Each crate is
downloaded from crates.io and checked against the checksum in Cargo.lock.

The extension modules also link the Rust standard library of the rustc
release named in their .comment section. The rust-src archive of that
release, checked against the SHA-256 that Rust publishes for it, supplies the
license files of rust-lang/rust, which are checked against known checksums,
all license files in the library tree, the licenses that the library's own
crates declare, and the library's Cargo.lock. Which crates have code in a
module is read from the names of its code units in the symbol table and from
the source paths in panic messages. The library's dependencies among them are
downloaded from crates.io and checked against the checksums in its
Cargo.lock.

The musllinux wheels, which the Alpine image installs, also include libgcc_s,
which the Rust code needs for unwinding. Each copy must come from a toolchain
in TOOLCHAIN in scripts/check-libraries.py, and its .comment section must name
the GCC release of the gcc extra artifact in package.yml. The license and
runtime library exception texts are taken from GCC's repository at that
release and checked against known checksums, and SOURCES lists the GCC,
musl-cross-make and rust-musl-cross archives that package.yml attaches to
each release as the library's source.

The run stops when a crate has no license files, when its license offers none
of the terms in ALLOWED, when the extension module of a wheel the images
install has code of a crate that neither the walk nor the standard library
accounts for, when the wheels of one package were built by several rustc
releases, when a wheel includes a library other than libgcc_s, or when a
libgcc_s does not match TOOLCHAIN and package.yml. Otherwise it writes each
crate's license files, the Rust and GCC texts, and a SOURCES file with the
URL and checksum of every archive, to licenses/<dist>-<version>/bundled/. Run
it whenever requirements.lock changes the version of rpds-py or regress.
"""

import functools
import hashlib
import importlib.util
import io
import json
import lzma
import pathlib
import re
import shutil
import sys
import tarfile
import textwrap
import tomllib
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

DISTS = ("rpds-py", "regress")
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
CRATE_URL = "https://static.crates.io/crates/{name}/{name}-{version}.crate"
LICENSE_FILE = re.compile(r"(LICEN[CS]E|COPYING|COPYRIGHT|NOTICE|UNLICENSE)([-._].*)?", re.I)

# Terms of the license in package.yml. A crate must offer at least one of
# them, and every term of the license of a standard library crate must be
# here; anything else needs a change to package.yml first.
ALLOWED = {
    "Apache-2.0",
    "Apache-2.0 WITH LLVM-exception",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MIT",
    "MPL-2.0",
}
SPDX_TERM = re.compile(r"[A-Za-z0-9.+-]+(?: WITH [A-Za-z0-9.+-]+)?")

# The wheels installed on Ubuntu and Alpine.
PLATFORMS = (
    r"manylinux\S*_x86_64",
    r"manylinux\S*_aarch64",
    r"musllinux_\d+_\d+_x86_64",
    r"musllinux_\d+_\d+_aarch64",
)

# The Rust targets of those wheels, with the cfg values that Cargo.toml files
# use to pick target-specific dependencies.
TARGETS = {
    f"{arch}-unknown-linux-{env}": {
        "target_os": {"linux"},
        "target_family": {"unix"},
        "target_vendor": {"unknown"},
        "target_arch": {arch},
        "target_env": {env},
        "target_abi": {""},
        "target_endian": {"little"},
        "target_pointer_width": {"64"},
        "target_has_atomic": {"8", "16", "32", "64", "ptr"} | ({"128"} if arch == "aarch64" else set()),
        "panic": {"unwind"},
        "unix": True,
        "windows": False,
    }
    for arch in ("x86_64", "aarch64")
    for env in ("gnu", "musl")
}

CFG_TOKEN = re.compile(
    r'(?P<space>\s+)|(?P<ident>[A-Za-z_][A-Za-z0-9_]*)|"(?P<string>[^"]*)"'
    r"|(?P<open>\()|(?P<close>\))|(?P<comma>,)|(?P<equals>=)"
)

# The libgcc_s that maturin copies into the musllinux wheels, named after the
# first 8 hex digits of the original's SHA-256, and the version string in its
# .comment section: "GCC: (<vendor>) <version>".
GCC_LIBRARY = re.compile(r"libgcc_s-([0-9a-f]{8})\.so\.1")
GCC_COMMENT = re.compile(rb"(GCC: \([^)\x00]*\) (\d+\.\d+\.\d+))\x00")
GCC_FILE = "https://gcc.gnu.org/git/?p=gcc.git;a=blob_plain;f={name};hb=refs/tags/releases/gcc-{version}"
# The extra artifacts in package.yml that hold the source of that libgcc_s.
GCC_SOURCES = {"gcc": "GCC", "musl-cross-make": "musl-cross-make", "rust-musl-cross": "rust-musl-cross"}
# COPYING3 (GPL version 3) and COPYING.RUNTIME (GCC Runtime Library Exception
# 3.1) have been the same in every GCC release since 2009. A release with other
# texts stops the run.
GCC_LICENSE_FILES = {
    "COPYING3": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    "COPYING.RUNTIME": "9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74",
}

# Panic messages carry the source path of the crate they come from:
# .../registry/src/index.crates.io-<hash>/<name>-<version>/src/...
CRATE_PATH = re.compile(rb"/registry/src/[^/\x00]+/([A-Za-z0-9_-]+?)-(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)/")
# Every crate with code in an extension module names its code units in the
# symbol table: <crate>.<hash>-cgu.<n>.
CODE_UNIT = re.compile(rb"(?<=\x00)([A-Za-z_][A-Za-z0-9_]*)\.[0-9a-z]+-cgu\.\d+(?=\x00)")

# The Rust standard library: the rustc version string in the .comment section
# of an extension module, the source paths of the crates.io crates that the
# library depends on, and the rust-src archive of the release, which holds the
# library's source and Cargo.lock.
RUSTC_COMMENT = re.compile(rb"(rustc version (\d+\.\d+\.\d+) \([0-9a-f]+ \d{4}-\d{2}-\d{2}\))\x00")
STD_CRATE_PATH = re.compile(rb"/rust/deps/([A-Za-z0-9_-]+?)-(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)/")
RUST_SOURCE = "https://static.rust-lang.org/dist/rust-src-{version}.tar.xz"
# COPYRIGHT, LICENSE-APACHE and LICENSE-MIT of rust-lang/rust, the same in
# releases 1.96.0 and 1.98.1. A release with other texts stops the run.
RUST_LICENSE_FILES = {
    "COPYRIGHT": "172020dbfd5b53a226dfde77616190a48dcff519b0bc0e6deb91a8450782c4af",
    "LICENSE-APACHE": "62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a",
    "LICENSE-MIT": "b71bd43a069ca0641a9ecfe585ca7b3c53b5cc1608f8b68321168698e28b5ea1",
}


def libgcc_toolchain():
    """TOOLCHAIN from scripts/check-libraries.py, which the build checks the installed copies against."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("check_libraries", ROOT / "scripts" / "check-libraries.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TOOLCHAIN


def gcc_extra_artifacts():
    """{name: (version, url, sha256)} of the GCC_SOURCES entries under extra-artifacts in package.yml."""
    text = (ROOT / "package.yml").read_text()
    found = {}
    for name in GCC_SOURCES:
        match = re.search(rf"^    {re.escape(name)}:\n((?:      \S.*\n)+)", text, re.M)
        fields = dict(re.findall(r"^      ([a-z0-9-]+): (\S+)$", match.group(1), re.M)) if match else {}
        if not {"version", "url", "sha256"} <= fields.keys():
            sys.exit(f"package.yml has no extra artifact {name} with version, url and sha256")
        found[name] = (fields["version"], fields["url"].replace("{version}", fields["version"]), fields["sha256"])
    return found


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "bundled-licenses.py"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def fetch_checked(url, sha256):
    data = fetch(url)
    if hashlib.sha256(data).hexdigest() != sha256:
        sys.exit(f"{url} does not match its SHA-256")
    return data


def cfg_matches(expr, target):
    """Evaluate the inside of a Cargo cfg(...) for one of TARGETS."""
    tokens = []
    pos = 0
    while pos < len(expr):
        match = CFG_TOKEN.match(expr, pos)
        if not match:
            sys.exit(f"cannot read cfg({expr})")
        if match.lastgroup != "space":
            tokens.append((match.lastgroup, match.group(match.lastgroup)))
        pos = match.end()
    tokens.append(("end", ""))
    pos = 0

    def take(kind):
        nonlocal pos
        if tokens[pos][0] != kind:
            sys.exit(f"cannot read cfg({expr})")
        pos += 1
        return tokens[pos - 1][1]

    def predicate():
        name = take("ident")
        if name in ("all", "any", "not"):
            take("open")
            values = []
            while tokens[pos][0] != "close":
                values.append(predicate())
                if tokens[pos][0] != "close":
                    take("comma")
            take("close")
            if name == "all":
                return all(values)
            if name == "any":
                return any(values)
            if len(values) != 1:
                sys.exit(f"cannot read cfg({expr})")
            return not values[0]
        if name not in target:
            sys.exit(f"cfg({expr}) uses {name}, which TARGETS does not define")
        if tokens[pos][0] == "equals":
            take("equals")
            return take("string") in target[name]
        return target[name] is True

    result = predicate()
    take("end")
    return result


def linked_dependencies(manifest):
    """Names of the crate's dependencies that are linked on any of TARGETS."""
    names = set()

    def add(table):
        for key, spec in (table or {}).items():
            names.add(spec.get("package", key) if isinstance(spec, dict) else key)

    add(manifest.get("dependencies"))
    for spec, table in manifest.get("target", {}).items():
        if spec.startswith("cfg(") and spec.endswith(")"):
            used = any(cfg_matches(spec[4:-1], target) for target in TARGETS.values())
        else:
            used = spec in TARGETS
        if used:
            add(table.get("dependencies"))
    return names


def tar_files(tar):
    return {m.name for m in tar.getmembers() if m.isfile()}


def tar_reader(tar, where):
    """A function that returns the content of a file in the archive, or stops the run."""

    def read(name):
        try:
            member = tar.getmember(name)
        except KeyError:
            member = None
        if member is None or not member.isfile():
            sys.exit(f"{where} does not contain {name}")
        return tar.extractfile(member).read()

    return read


def read_crate(name, version, checksum):
    """Download a crate and return its manifest and license files."""
    url = CRATE_URL.format(name=name, version=version)
    prefix = f"{name}-{version}/"
    with tarfile.open(fileobj=io.BytesIO(fetch_checked(url, checksum))) as tar:
        manifest = tomllib.loads(tar.extractfile(f"{prefix}Cargo.toml").read().decode())
        paths = {
            m.name
            for m in tar.getmembers()
            if m.isfile() and m.name.count("/") == 1 and LICENSE_FILE.fullmatch(m.name[len(prefix):])
        }
        license_file = manifest["package"].get("license-file")
        if license_file:
            parts = pathlib.PurePosixPath(license_file).parts
            if not parts or parts[0] == "/" or ".." in parts:
                sys.exit(f"{url} names the license file {license_file!r}, outside the crate")
            paths.add(prefix + license_file)
        files = {}
        for path in sorted(paths):
            try:
                files[path[len(prefix):]] = tar.extractfile(path).read()
            except KeyError:
                sys.exit(f"{url} does not contain {path}")
    return url, manifest, files


def declared_license(where, manifest, files):
    if not files:
        sys.exit(f"{where} contains no license file")
    license = manifest["package"].get("license")
    if not isinstance(license, str) or not license:
        sys.exit(f"{where} declares no SPDX license")
    return license


def unit_name(manifest):
    """The crate name that the code units of a package's library carry."""
    return (manifest.get("lib", {}).get("name") or manifest["package"]["name"]).replace("-", "_")


def walk(dist, sdist):
    """The crates linked into the extension module, {(name, version): (license, files, url, sha256)},
    and the names their code units carry."""
    with tarfile.open(fileobj=io.BytesIO(sdist)) as tar:
        top = tar.getnames()[0].split("/")[0]
        try:
            manifest = tomllib.loads(tar.extractfile(f"{top}/Cargo.toml").read().decode())
            lock = tomllib.loads(tar.extractfile(f"{top}/Cargo.lock").read().decode())
        except KeyError:
            sys.exit(f"the {dist} source release has no Cargo.toml and Cargo.lock at its top level")
    packages = {}
    for package in lock["package"]:
        packages.setdefault(package["name"], []).append(package)

    def resolve(entry):
        # A Cargo.lock dependency is "name", or "name version" when several versions are locked.
        name, _, rest = entry.partition(" ")
        candidates = packages.get(name, [])
        if rest:
            candidates = [p for p in candidates if p["version"] == rest.split(" ")[0]]
        if len(candidates) != 1:
            sys.exit(f"{dist}: cannot resolve the Cargo.lock dependency {entry!r}")
        return candidates[0]

    root = resolve(manifest["package"]["name"])
    if "source" in root:
        sys.exit(f"{dist}: {root['name']} in Cargo.lock is not the crate of the source release")
    crates = {}
    units = {unit_name(manifest)}
    seen = {(root["name"], root["version"])}
    queue = [(root, manifest)]
    while queue:
        package, manifest = queue.pop()
        linked = linked_dependencies(manifest)
        for entry in package.get("dependencies", []):
            dep = resolve(entry)
            key = (dep["name"], dep["version"])
            if dep["name"] not in linked or key in seen:
                continue
            seen.add(key)
            if dep.get("source") != REGISTRY or "checksum" not in dep:
                sys.exit(f"{dist}: {dep['name']} {dep['version']} does not come from crates.io")
            url, dep_manifest, files = read_crate(dep["name"], dep["version"], dep["checksum"])
            lib = dep_manifest.get("lib", {})
            if lib.get("proc-macro") or lib.get("proc_macro"):
                continue
            crates[key] = (declared_license(url, dep_manifest, files), files, url, dep["checksum"])
            units.add(unit_name(dep_manifest))
            queue.append((dep, dep_manifest))
    return root, crates, units


def check_terms(crates):
    for (name, version), (license, *_) in sorted(crates.items()):
        if "(" in license or " AND " in license:
            sys.exit(f"{name} {version} is licensed under {license}, which this script cannot check")
        if not {term.strip() for term in re.split(r" OR |/", license)} & ALLOWED:
            sys.exit(f"{name} {version} is licensed under {license}, which offers no term in ALLOWED")


def check_wheels(dist, release, python, crates, lock, toolchain):
    """Check the wheels the images install.

    Return the GCC version strings and the paths of the libgcc_s they
    include, the rustc version string of their extension modules, and the
    code unit names and standard library source paths in those modules.
    """
    hashes = set(re.findall(r"sha256:([0-9a-f]{64})", lock))
    version = release["info"]["version"]
    stem = re.escape(dist.replace("-", "_"))
    gcc = set()
    gcc_paths = set()
    rustc = set()
    units = set()
    std_paths = set()
    for platform in PLATFORMS:
        pattern = re.compile(rf"{stem}-{re.escape(version)}-{python}-{python}-{platform}\.whl", re.I)
        wheels = [f for f in release["urls"] if pattern.fullmatch(f["filename"])]
        if len(wheels) != 1:
            sys.exit(f"{dist} {version} has {len(wheels)} {python} wheels for {platform}")
        wheel = wheels[0]
        if wheel["digests"]["sha256"] not in hashes:
            sys.exit(f"requirements.lock does not pin {wheel['filename']}")
        with zipfile.ZipFile(io.BytesIO(fetch_checked(wheel["url"], wheel["digests"]["sha256"]))) as archive:
            libraries = [
                n for n in archive.namelist() if n.split("/")[0].endswith(".libs") and not n.endswith("/")
            ]
            modules = [n for n in archive.namelist() if n.endswith(".so") and n not in libraries]
            if len(modules) != 1:
                sys.exit(f"{wheel['filename']} has {len(modules)} extension modules")
            binary = archive.read(modules[0])
            for library in libraries:
                match = GCC_LIBRARY.fullmatch(library.split("/", 1)[1])
                if not match:
                    sys.exit(f"{wheel['filename']} includes {library}, which this script does not know")
                if match.group(1) not in toolchain:
                    sys.exit(
                        f"{wheel['filename']} includes {library}, which is not from a toolchain in TOOLCHAIN "
                        "in scripts/check-libraries.py: find the image that built the wheel, and update "
                        "TOOLCHAIN and the GCC sources in package.yml"
                    )
                gcc_paths.add(f"{library} ({toolchain[match.group(1)]})")
                found = set(GCC_COMMENT.findall(archive.read(library)))
                if len(found) != 1:
                    sys.exit(f"{wheel['filename']}: cannot tell the GCC release of {library}")
                gcc |= {(comment.decode(), gcc_version.decode()) for comment, gcc_version in found}
        found = {(m.group(1).decode(), m.group(2).decode()) for m in CRATE_PATH.finditer(binary)}
        if not found:
            sys.exit(f"{wheel['filename']} names no Rust crate")
        unknown = sorted(found - set(crates))
        if unknown:
            names = ", ".join(f"{n} {v}" for n, v in unknown)
            sys.exit(f"{wheel['filename']} links {names}, which the Cargo.lock walk did not find")
        releases = {(comment.decode(), number.decode()) for comment, number in RUSTC_COMMENT.findall(binary)}
        if len(releases) != 1:
            sys.exit(f"{wheel['filename']}: cannot tell the rustc release of {modules[0]}")
        rustc |= releases
        module_units = {unit.decode() for unit in CODE_UNIT.findall(binary)}
        if not module_units:
            sys.exit(f"{wheel['filename']}: {modules[0]} has no code unit names in its symbol table")
        units |= module_units
        std_paths |= {(m.group(1).decode(), m.group(2).decode()) for m in STD_CRATE_PATH.finditer(binary)}
    if len(gcc) > 1:
        sys.exit(f"the {dist} {version} wheels include libgcc_s from several GCC builds: {sorted(gcc)}")
    if len(rustc) > 1:
        sys.exit(f"the {dist} {version} wheels were built by several rustc releases: {sorted(rustc)}")
    return gcc, sorted(gcc_paths), rustc.pop(), units, std_paths


@functools.cache
def rust_source(version):
    """The URL, SHA-256 and uncompressed content of the rust-src archive of a release."""
    url = RUST_SOURCE.format(version=version)
    archive = url.rpartition("/")[2]
    sums = fetch(f"{url}.sha256").decode()
    match = re.search(rf"^([0-9a-f]{{64}})  {re.escape(archive)}$", sums, re.M)
    if not match:
        sys.exit(f"{archive}.sha256 does not list {archive}")
    return url, match.group(1), lzma.decompress(fetch_checked(url, match.group(1)))


def rust_std(dist, version, units, std_paths, extension_units):
    """The Rust standard library of a release: the license files in its rust-src
    archive, the archive's URL and SHA-256, the licenses of its own crates whose
    code is in the extension modules, and the crates.io crates it depends on whose
    code is there."""
    url, sha256, data = rust_source(version)
    archive = url.rpartition("/")[2]
    top = f"rust-src-{version}/"
    library = f"{top}rust-src/lib/rustlib/src/rust/library/"
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        read = tar_reader(tar, archive)
        files = {}
        for name, expected in RUST_LICENSE_FILES.items():
            text = read(top + name)
            if hashlib.sha256(text).hexdigest() != expected:
                sys.exit(f"{name} in {archive} differs from the text in RUST_LICENSE_FILES")
            files[name] = text
        # Newer archives also vendor the crates.io dependencies; those are
        # taken from crates.io below, like the rest.
        in_tree_licenses = {}
        for name in sorted(tar_files(tar)):
            path = name[len(library):]
            if not name.startswith(library) or path.startswith("vendor/"):
                continue
            if ".." in pathlib.PurePosixPath(path).parts:
                sys.exit(f"{archive} contains {name}, outside the library tree")
            if LICENSE_FILE.fullmatch(path.rpartition("/")[2]):
                files[f"library/{path}"] = read(name)
            elif path.endswith("Cargo.toml"):
                package = tomllib.loads(read(name).decode()).get("package", {})
                if isinstance(package.get("license"), str):
                    in_tree_licenses.setdefault(package["name"].replace("-", "_"), set()).add(package["license"])
        lock = tomllib.loads(read(f"{library}Cargo.lock").decode())
    registry = {}
    in_tree = set()
    for package in lock["package"]:
        unit = package["name"].replace("-", "_")
        if package.get("source") == REGISTRY:
            registry.setdefault(unit, []).append(package)
        elif "source" not in package:
            in_tree.add(unit)
    std_crates = {}
    named = set(std_paths)
    for unit in sorted(units):
        if unit in in_tree:
            licenses = in_tree_licenses.get(unit, set())
            if len(licenses) != 1:
                sys.exit(f"{archive}: cannot tell the license of the standard library crate {unit}")
            std_crates[unit] = licenses.pop()
        elif len(registry.get(unit, [])) > 1:
            sys.exit(f"the Rust {version} standard library depends on several versions of {unit}")
        elif unit in registry:
            named.add((registry[unit][0]["name"], registry[unit][0]["version"]))
        elif unit not in extension_units:
            sys.exit(
                f"the {dist} extension modules have code of the crate {unit}, which neither "
                f"the Cargo.lock walk nor the Rust {version} standard library accounts for"
            )
    for unit, license in sorted(std_crates.items()):
        terms = set(SPDX_TERM.findall(license)) - {"AND", "OR"}
        if not terms or terms - ALLOWED:
            sys.exit(f"the standard library crate {unit} is licensed under {license}, with terms not in ALLOWED")
    locked = {(p["name"], p["version"]): p for packages in registry.values() for p in packages}
    crates = {}
    for name, crate_version in sorted(named):
        package = locked.get((name, crate_version))
        if not package or "checksum" not in package:
            sys.exit(f"the Rust {version} standard library does not depend on {name} {crate_version} from crates.io")
        crate_url, manifest, texts = read_crate(name, crate_version, package["checksum"])
        license = declared_license(crate_url, manifest, texts)
        crates[(name, crate_version)] = (license, texts, crate_url, package["checksum"])
    check_terms(crates)
    return files, url, sha256, std_crates, crates


def gcc_license_texts(version):
    """The license texts of GCC's runtime library at a release."""
    return {
        name: fetch_checked(GCC_FILE.format(name=name, version=version), sha256)
        for name, sha256 in GCC_LICENSE_FILES.items()
    }


def write_bundle(dist, version, sdist, root, crates, rustc, rust, gcc, gcc_paths, gcc_extras):
    intro = (
        f"{dist} {version} is a Rust extension module built with PyO3. Its Linux "
        "wheels link a static build of the Rust crates below but include only "
        f"{dist}'s own license. The crates are the dependencies of the extension "
        f"crate {root['name']} in the Cargo.lock of the source release that are "
        "linked on Linux; build dependencies and procedural macros run only while "
        "building and are left out. Each directory holds the license files from the "
        "crate's archive on crates.io, and the license after each crate is the one "
        "it declares."
    )
    sources = [textwrap.fill(intro, 76, break_on_hyphens=False), ""]
    files = {}
    for (name, crate_version), (_, texts, *_) in crates.items():
        files.update({f"{name}-{crate_version}/{file_name}": text for file_name, text in texts.items()})
    entries = [f"{dist} {version}", f"  {sdist['url']}", f"  sha256:{sdist['digests']['sha256']}"]
    for (name, crate_version), (license, _, url, sha256) in sorted(crates.items()):
        entries += [f"{name} {crate_version} ({license})", f"  {url}", f"  sha256:{sha256}"]

    comment, rust_version = rustc
    rust_files, rust_url, rust_sha256, std_crates, std_deps = rust
    directory = f"rust-std-{rust_version}"
    intro = (
        "The extension module also links the Rust standard library. Its .comment "
        f"section reads \"{comment}\". The Rust entry below is the rust-src archive "
        "of that release, with the SHA-256 from its .sha256 file, and the "
        f"{directory} directory holds the license files of rust-lang/rust and every "
        "license file in the archive's library tree. Under the Rust entry are the "
        "standard library's own crates that have code in the extension module, with "
        "the licenses they declare. The crates after it are the standard library's "
        "dependencies from crates.io that have code in the module, checked against "
        f"the checksums in its Cargo.lock; their license files are in {directory} as "
        "well. Code is assigned to crates by the names of the module's code units "
        "and the source paths in its panic messages."
    )
    sources += [textwrap.fill(intro, 76, break_on_hyphens=False), ""]
    files.update({f"{directory}/{path}": text for path, text in rust_files.items()})
    entries += [f"Rust {rust_version} standard library", f"  {rust_url}", f"  sha256:{rust_sha256}"]
    entries += [f"  {unit} ({license})" for unit, license in sorted(std_crates.items())]
    for (name, crate_version), (license, texts, url, sha256) in sorted(std_deps.items()):
        files.update({f"{directory}/{name}-{crate_version}/{file_name}": text for file_name, text in texts.items()})
        entries += [f"{name} {crate_version} ({license})", f"  {url}", f"  sha256:{sha256}"]

    for comment, gcc_version in sorted(gcc):
        attached = gcc_extras["gcc"][0]
        if gcc_version != attached:
            sys.exit(f"the {dist} wheels include libgcc_s from GCC {gcc_version}, but package.yml has GCC {attached}")
        intro = (
            "The musllinux wheels, which the Alpine image installs, also include libgcc_s, "
            f"which the Rust code needs for unwinding: {' and '.join(gcc_paths)}. It is "
            "licensed under GPL-3.0-or-later with the GCC Runtime Library Exception 3.1, "
            "and both texts are in the libgcc directory, taken from GCC's repository at "
            f"release {gcc_version}. Its .comment section reads \"{comment}\". maturin "
            "copied it from the ghcr.io/rust-cross/rust-musl-cross image that built the "
            "wheel, and the hex digits in its name are the start of the original's "
            "SHA-256: the libgcc_s.so.1 of the images that rust-musl-cross built from "
            "the commit below. Those images build GCC with musl-cross-make, which "
            "applies its own patches to GCC, and with the config.mak of rust-musl-cross. "
            "The last three entries below are the corresponding source of the library: "
            "the GCC release, and the two repositories at the commits used. They are "
            "attached to the GitHub release of each version of the image, which the "
            "last line of the image's source file links to."
        )
        sources += [textwrap.fill(intro, 76, break_on_hyphens=False), ""]
        files.update({f"libgcc/{file_name}": text for file_name, text in gcc_license_texts(gcc_version).items()})
        for name, label in GCC_SOURCES.items():
            extra_version, url, sha256 = gcc_extras[name]
            license = " (GPL-3.0-or-later WITH GCC-exception-3.1)" if name == "gcc" else ""
            entries += [f"{label} {extra_version}{license}", f"  {url}", f"  sha256:{sha256}"]

    base = ROOT / "licenses"
    for old in base.glob(f"{dist}-[0-9]*"):
        shutil.rmtree(old)
    out = base / f"{dist}-{version}" / "bundled"
    for path, text in sorted(files.items()):
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(text)
    (out / "SOURCES").write_text("\n".join(sources + entries) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")


def main():
    lock = (ROOT / "requirements.lock").read_text()
    match = re.search(r"--python-version 3\.(\d+)", lock)
    if not match:
        sys.exit("requirements.lock does not name the Python version it resolves for")
    python = f"cp3{match.group(1)}"
    toolchain = libgcc_toolchain()
    gcc_extras = gcc_extra_artifacts()

    for dist in DISTS:
        match = re.search(rf"^{re.escape(dist)}==([^\s;]+)", lock, re.M | re.I)
        if not match:
            sys.exit(f"requirements.lock does not pin {dist}")
        version = match.group(1)
        release = json.loads(fetch(f"https://pypi.org/pypi/{dist}/{version}/json"))
        sdists = [f for f in release["urls"] if f["packagetype"] == "sdist"]
        if len(sdists) != 1:
            sys.exit(f"{dist} {version} has {len(sdists)} source releases on PyPI")
        sdist = sdists[0]
        root, crates, extension_units = walk(dist, fetch_checked(sdist["url"], sdist["digests"]["sha256"]))
        check_terms(crates)
        gcc, gcc_paths, rustc, units, std_paths = check_wheels(dist, release, python, crates, lock, toolchain)
        rust = rust_std(dist, rustc[1], units, std_paths, extension_units)
        write_bundle(dist, version, sdist, root, crates, rustc, rust, gcc, gcc_paths, gcc_extras)


if __name__ == "__main__":
    main()
