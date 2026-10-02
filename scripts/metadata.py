"""Write source URLs, build info and license files for the running venv.

Usage: metadata.py REPORT OUTDIR [EXTRA]

REPORT is the JSON written by `pip install --report`. Run this with the
venv's own interpreter so importlib.metadata lists what the venv contains,
including pip itself, which comes from the distro and is not in the report.

EXTRA holds license files that a wheel contains code for but does not ship,
in one directory per distribution named <name>-<version>. Each is copied
into that distribution's license directory, and the run fails when the
installed version differs, so the files cannot go stale unnoticed.
"""

import json
import pathlib
import platform
import re
import shutil
import sys
from importlib import metadata

LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def license_files(dist):
    for path in dist.files or ():
        if len(path.parts) < 2 or not path.parts[0].endswith(".dist-info"):
            continue
        if path.parts[1] == "licenses" or path.name.upper().startswith(LICENSE_NAMES):
            yield path


def copy_extra(extra, dists, licenses):
    installed = {normalize(d.metadata["Name"]): d for d in dists}
    problems = []
    for src in sorted(p for p in pathlib.Path(extra).iterdir() if p.is_dir()):
        name, _, version = src.name.rpartition("-")
        dist = installed.get(normalize(name))
        if dist is None:
            problems.append(f"{src.name}: {name} is not installed")
        elif dist.version != version:
            problems.append(f"{src.name}: {name} {dist.version} is installed")
        else:
            shutil.copytree(src, licenses / dist.metadata["Name"], dirs_exist_ok=True)
    return problems


def main(report_path, outdir, extra=None):
    out = pathlib.Path(outdir)
    report = json.loads(pathlib.Path(report_path).read_text())
    installed = sorted(report["install"], key=lambda item: item["metadata"]["name"].lower())
    lines = []
    for item in installed:
        download = item["download_info"]
        lines.append(f"{download['url']}#sha256={download['archive_info']['hashes']['sha256']}\n")
    (out / "source").write_text("".join(lines))

    dists = sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower())
    info = [f"python {platform.python_version()}"]
    info += [f"{d.metadata['Name']}=={d.version}" for d in dists]
    (out / "buildinfo").write_text("\n".join(info) + "\n")

    missing = []
    for dist in dists:
        name = dist.metadata["Name"]
        files = list(license_files(dist))
        if not files:
            missing.append(name)
        for path in files:
            rel = path.parts[2:] if path.parts[1] == "licenses" else path.parts[1:]
            target = out / "licenses" / name / pathlib.Path(*rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path.locate(), target)
    if missing:
        sys.exit(f"no license file found for: {', '.join(missing)}")

    if extra:
        problems = copy_extra(extra, dists, out / "licenses")
        if problems:
            sys.exit(
                "license files in licenses/ do not match the installed packages:\n  "
                + "\n  ".join(problems)
                + "\nrun scripts/bundled-licenses.py to refresh them"
            )


if __name__ == "__main__":
    main(*sys.argv[1:])
