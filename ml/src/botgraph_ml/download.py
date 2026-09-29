"""Download public datasets and extract only the flow files we need.

    python -m botgraph_ml.download ctu13     # one archive, flow files extracted
    python -m botgraph_ml.download iot23     # individual captures listed in params.yaml

Layout produced:
    ml/data/raw/ctu13/scenario=<N>/flows.binetflow
    ml/data/raw/iot23/<capture-name>/conn.log.labeled
"""

from __future__ import annotations

import argparse
import shutil
import tarfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import requests
from tqdm import tqdm

from botgraph_ml.config import load_params, repo_path

CHUNK = 1 << 20


def download(url: str, dest: Path) -> Path:
    """Stream ``url`` to ``dest``, resuming a previous partial download if present."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}

    with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
        if resp.status_code == 416:  # nothing left to fetch
            part.rename(dest)
            return dest
        resp.raise_for_status()
        if offset and resp.status_code != 206:  # server ignored Range: start over
            offset = 0
        total = int(resp.headers.get("Content-Length", 0)) + offset
        mode = "ab" if offset else "wb"
        with (
            part.open(mode) as fh,
            tqdm(
                total=total or None, initial=offset, unit="B", unit_scale=True, desc=dest.name
            ) as bar,
        ):
            for chunk in resp.iter_content(CHUNK):
                fh.write(chunk)
                bar.update(len(chunk))
    part.rename(dest)
    return dest


def _copy_member(tar: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    # extractfile + our own target path: archive paths never touch the filesystem,
    # so a crafted archive cannot write outside out_dir (no path traversal).
    src = tar.extractfile(member)
    if src is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst, CHUNK)


def extract_matching(
    archive: Path, out_dir: Path, target_for: Callable[[PurePosixPath], Path | None]
) -> list[Path]:
    """Walk ``archive`` once and copy each member for which ``target_for`` returns a path.

    Uses random-access mode ("r:*"), not stream mode ("r|*"): in stream mode tarfile skips
    unwanted members by slicing an ever-growing decompressed buffer, which is quadratic and
    stalls for hours on CTU-13's multi-GB, highly compressible pcaps.
    """
    written: list[Path] = []
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            rel = target_for(PurePosixPath(member.name))
            if rel is None:
                continue
            target = out_dir / rel
            _copy_member(tar, member, target)
            written.append(target)
    return written


def ctu13_target(path: PurePosixPath) -> Path | None:
    """``CTU-13-Dataset/<N>/<capture>.binetflow`` -> ``scenario=<N>/flows.binetflow``."""
    if path.suffix != ".binetflow" or not path.parent.name.isdigit():
        return None
    return Path(f"scenario={int(path.parent.name)}") / "flows.binetflow"


def iot23_target(path: PurePosixPath) -> Path | None:
    """``.../<capture-name>/bro/conn.log.labeled`` -> ``<capture-name>/conn.log.labeled``."""
    if path.name != "conn.log.labeled":
        return None
    capture = next((p for p in reversed(path.parent.parts) if p != "bro"), None)
    return Path(capture) / "conn.log.labeled" if capture else None


TARGETS = {"ctu13": ctu13_target, "iot23": iot23_target}


def download_iot23(params: dict[str, object], out_dir: Path) -> list[Path]:
    """Fetch each listed capture's labelled Zeek conn log directly (no archive)."""
    cfg = params["iot23"]
    assert isinstance(cfg, dict)
    written = []
    for capture in cfg["captures"]:
        # "CTU-Honeypot-Capture-7-1/Somfy-01" -> local dir "CTU-Honeypot-Capture-7-1_Somfy-01"
        target = out_dir / capture.replace("/", "_") / "conn.log.labeled"
        written.append(download(f"{cfg['base_url']}/{capture}/bro/conn.log.labeled", target))
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("dataset", choices=sorted(TARGETS))
    parser.add_argument("--url", help="override the archive URL from params.yaml")
    parser.add_argument("--archive", type=Path, help="use an already-downloaded archive")
    parser.add_argument("--keep-archive", action="store_true")
    args = parser.parse_args(argv)

    params = load_params()
    raw_dir = repo_path(params["data"]["raw_dir"])
    if args.dataset == "iot23":
        files = download_iot23(params, raw_dir / "iot23")
        print(f"downloaded {len(files)} captures into {raw_dir / 'iot23'}")
        return
    url: str = args.url or params[args.dataset]["url"]
    archive: Path = args.archive or raw_dir / "archives" / url.rsplit("/", 1)[-1]
    if not args.archive:
        download(url, archive)

    out_dir = raw_dir / args.dataset
    written = extract_matching(archive, out_dir, TARGETS[args.dataset])
    if not written:
        raise SystemExit(f"no matching files found in {archive}; has the archive layout changed?")
    print(f"extracted {len(written)} files into {out_dir}")

    if not args.archive and not args.keep_archive:
        archive.unlink()


if __name__ == "__main__":
    main()
