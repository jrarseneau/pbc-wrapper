#!/usr/bin/env python3
"""
Build a Slackware .txz package containing:
  - proxmox-backup-client  (statically linked, from Proxmox APT repo)
  - pxar                   (statically linked, from Proxmox APT repo)
  - pbc-wrapper            (from this repository)

All three binaries are installed into /usr/local/bin.

Usage:
  python3 build.py [--distro bookworm|trixie] [--output-dir DIR]
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PBS_REPO_BASE = "http://download.proxmox.com/debian/pbs-client"

# Packages to pull from the repo, in priority order.
# proxmox-backup-client-static contains both proxmox-backup-client and pxar;
# pxar may also be a separate package on some distro releases.
WANTED_PACKAGES = ["proxmox-backup-client-static", "pxar-static", "pxar"]

INSTALL_BIN = "usr/local/bin"

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent

# ---------------------------------------------------------------------------
# APT Packages index
# ---------------------------------------------------------------------------

def fetch_packages_index(distro: str) -> dict[str, dict]:
    url = f"{PBS_REPO_BASE}/dists/{distro}/main/binary-amd64/Packages"
    print(f"Fetching packages index: {url}")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            content = resp.read().decode()
    except Exception as e:
        print(f"ERROR: failed to fetch packages index: {e}", file=sys.stderr)
        sys.exit(1)

    packages: dict[str, dict] = {}
    current: dict[str, str] = {}
    for line in content.splitlines():
        if line == "":
            if "Package" in current:
                packages[current["Package"]] = current
            current = {}
        elif line.startswith(" "):
            # continuation line — append to last key
            if current:
                last = list(current)[-1]
                current[last] += "\n" + line.lstrip()
        elif ": " in line:
            key, _, val = line.partition(": ")
            current[key] = val
    if "Package" in current:
        packages[current["Package"]] = current

    return packages


def resolve_packages(index: dict[str, dict]) -> list[dict]:
    """Return package info dicts for the packages we want, deduplicating by binary content."""
    found = []
    seen_filenames: set[str] = set()
    for name in WANTED_PACKAGES:
        if name in index:
            info = index[name]
            fname = Path(info["Filename"]).name
            if fname not in seen_filenames:
                found.append(info)
                seen_filenames.add(fname)
                print(f"  Found: {name} {info['Version']}")
    return found


# ---------------------------------------------------------------------------
# Download & verify
# ---------------------------------------------------------------------------

def download_deb(info: dict, dest_dir: Path) -> Path:
    filename_path = info["Filename"]
    url = f"{PBS_REPO_BASE}/{filename_path}"
    dest = dest_dir / Path(filename_path).name

    print(f"Downloading {Path(filename_path).name} ...", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if "SHA256" in info:
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        if digest != info["SHA256"]:
            print(f"\nERROR: SHA256 mismatch for {dest.name}", file=sys.stderr)
            print(f"  expected: {info['SHA256']}", file=sys.stderr)
            print(f"  got:      {digest}", file=sys.stderr)
            sys.exit(1)
        print("OK (SHA256 verified)")
    else:
        print("OK")

    return dest


# ---------------------------------------------------------------------------
# .deb extraction
# ---------------------------------------------------------------------------

def extract_binaries_from_deb(deb_path: Path, dest_bin_dir: Path) -> list[str]:
    """Extract all files from usr/bin/ inside a .deb into dest_bin_dir."""
    extracted: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # A .deb is an ar archive; unpack it
        result = subprocess.run(
            ["ar", "x", str(deb_path)],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"ERROR: ar failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)

        # Find data.tar.* (may be .xz, .gz, .zst, or uncompressed)
        data_tars = list(tmp.glob("data.tar.*")) + list(tmp.glob("data.tar"))
        if not data_tars:
            print(f"ERROR: no data.tar.* found in {deb_path.name}", file=sys.stderr)
            sys.exit(1)

        extract_dir = tmp / "data"
        extract_dir.mkdir()
        result = subprocess.run(
            ["tar", "xf", str(data_tars[0]), "-C", str(extract_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"ERROR: tar failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)

        # Grab everything from usr/bin/
        src_bin = extract_dir / "usr" / "bin"
        if not src_bin.exists():
            print(f"  WARNING: no usr/bin/ in {deb_path.name}")
            return extracted

        for binary in sorted(src_bin.iterdir()):
            if binary.is_file():
                dest = dest_bin_dir / binary.name
                shutil.copy2(binary, dest)
                dest.chmod(0o755)
                extracted.append(binary.name)
                print(f"  Extracted: {binary.name}")

    return extracted


# ---------------------------------------------------------------------------
# Package assembly
# ---------------------------------------------------------------------------

def write_doinst(install_dir: Path) -> None:
    doinst = install_dir / "doinst.sh"
    doinst.write_text(
        "#!/bin/sh\n"
        "# Create default profile directory if it does not exist\n"
        "mkdir -p /etc/pbs-client/repos.d\n"
        "chmod 700 /etc/pbs-client/repos.d\n"
    )
    doinst.chmod(0o755)


def build_txz(version: str, pkg_dir: Path, output_dir: Path) -> Path:
    arch = "x86_64"
    build = "1"
    pkg_name = f"pbc-wrapper-{version}-{arch}-{build}.txz"
    output_path = output_dir / pkg_name

    result = subprocess.run(
        ["tar", "--create", "--xz", "--owner=root", "--group=root",
         "--file", str(output_path), "--directory", str(pkg_dir), "."],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: tar failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def check_tools() -> None:
    missing = [t for t in ("ar", "tar") if not shutil.which(t)]
    if missing:
        print(f"ERROR: required tools not found in PATH: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build pbc-wrapper Slackware .txz package",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--distro",
        default="bookworm",
        choices=["bookworm", "trixie"],
        help="Proxmox APT distro to pull binaries from",
    )
    parser.add_argument(
        "--output-dir",
        default=str(SCRIPT_DIR),
        help="Directory to write the .txz into",
    )
    parser.add_argument(
        "--list-packages",
        action="store_true",
        help="Print all packages available in the index and exit",
    )
    args = parser.parse_args()

    check_tools()

    index = fetch_packages_index(args.distro)

    if args.list_packages:
        print(f"\nPackages available in {args.distro}:")
        for name in sorted(index):
            print(f"  {name}  {index[name]['Version']}")
        return

    print("\nResolving packages:")
    packages = resolve_packages(index)
    if not packages:
        print("ERROR: none of the wanted packages were found in the index.", file=sys.stderr)
        print(f"Wanted: {WANTED_PACKAGES}", file=sys.stderr)
        print("Run with --list-packages to see what is available.", file=sys.stderr)
        sys.exit(1)

    # Derive package version from proxmox-backup-client-static if present
    version = packages[0]["Version"].split("-")[0]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as workdir:
        workdir = Path(workdir)
        deb_dir = workdir / "debs"
        deb_dir.mkdir()

        # Staging tree
        pkg_dir = workdir / "pkg"
        bin_dir = pkg_dir / INSTALL_BIN
        bin_dir.mkdir(parents=True)
        install_dir = pkg_dir / "install"
        install_dir.mkdir()

        # Download and extract each .deb
        all_binaries: list[str] = []
        print()
        for info in packages:
            deb = download_deb(info, deb_dir)
            binaries = extract_binaries_from_deb(deb, bin_dir)
            all_binaries.extend(binaries)

        if not all_binaries:
            print("ERROR: no binaries were extracted from the downloaded packages.", file=sys.stderr)
            sys.exit(1)

        # pbc-wrapper from this repo
        wrapper_src = REPO_DIR / "pbc-wrapper"
        if not wrapper_src.is_file():
            print(f"ERROR: pbc-wrapper not found at {wrapper_src}", file=sys.stderr)
            sys.exit(1)
        shutil.copy2(wrapper_src, bin_dir / "pbc-wrapper")
        (bin_dir / "pbc-wrapper").chmod(0o755)
        print(f"  Copied:    pbc-wrapper")

        # install/ support files
        slack_desc_src = SCRIPT_DIR / "slack-desc"
        shutil.copy2(slack_desc_src, install_dir / "slack-desc")
        write_doinst(install_dir)

        # Build the package
        print(f"\nBuilding package (version {version})...")
        pkg_path = build_txz(version, pkg_dir, output_dir)

    size_kb = pkg_path.stat().st_size // 1024
    print(f"Created: {pkg_path.name}  ({size_kb} KB)")
    print("\nInstalls:")
    for name in sorted(set(all_binaries) | {"pbc-wrapper"}):
        print(f"  /usr/local/bin/{name}")
    print("  /etc/pbs-client/repos.d/  (created by doinst.sh)")


if __name__ == "__main__":
    main()
