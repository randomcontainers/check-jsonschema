"""Check the shared libraries that the installed wheels carry.

Usage: check-libraries.py [GCC_VERSION]

Run this with the venv's own interpreter. A wheel repaired by maturin or
auditwheel keeps copies of the shared libraries it needs in <package>.libs/.
The only ones that package.yml and licenses/ cover are the copies of
libgcc_s in the musllinux wheels of rpds-py and regress, and the sources
that package.yml attaches to each release are those of the toolchain in
TOOLCHAIN. On Alpine, pass the version of the gcc entry in package.yml; on
Ubuntu, pass nothing, and no wheel may carry a library.

The run fails on any other library, on a libgcc_s from another toolchain or
GCC release, and on Alpine when no wheel carries libgcc_s any more, so that
package.yml, licenses/ and the README are reviewed first.
"""

import pathlib
import re
import sys
import sysconfig

# libgcc_s.so.1 of the ghcr.io/rust-cross/rust-musl-cross images that built
# the wheels, by the first 8 hex digits of its SHA-256, which maturin puts in
# the name of its copy. Both are GCC as built by musl-cross-make with the
# config.mak of rust-musl-cross, at the commits that package.yml pins.
TOOLCHAIN = {
    "f685abf1": "x86_64-musl",
    "0bf60adc": "aarch64-musl",
}
LIBGCC = re.compile(r"libgcc_s-([0-9a-f]{8})\.so\.1")
GCC_COMMENT = re.compile(rb"GCC: \([^)\x00]*\) [^\x00]*(?=\x00)")


def main(gcc_version=None):
    site = pathlib.Path(sysconfig.get_path("platlib"))
    libraries = sorted(p for d in site.glob("*.libs") for p in d.rglob("*") if p.is_file())
    expected = f"GCC: (GNU) {gcc_version}" if gcc_version else None
    problems = []
    for path in libraries:
        name = path.relative_to(site)
        match = LIBGCC.fullmatch(path.name)
        if expected is None or not match:
            problems.append(f"{name}: package.yml and licenses/ do not cover this library")
            continue
        if match.group(1) not in TOOLCHAIN:
            problems.append(f"{name}: not from a toolchain in TOOLCHAIN, whose sources package.yml attaches")
            continue
        found = sorted({c.decode(errors="replace") for c in GCC_COMMENT.findall(path.read_bytes())})
        if found != [expected]:
            problems.append(f"{name}: built by {', '.join(found) or 'an unknown compiler'}, expected {expected}")
            continue
        print(f"{name}: {expected}, {TOOLCHAIN[match.group(1)]}")
    if expected and not libraries:
        problems.append("no wheel carries libgcc_s any more: review the GCC entries and license in package.yml")
    if problems:
        sys.exit("\n".join(problems))


if __name__ == "__main__":
    if len(sys.argv) > 2:
        sys.exit("usage: check-libraries.py [GCC_VERSION]")
    main(*sys.argv[1:])
