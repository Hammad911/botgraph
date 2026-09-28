"""Normalize raw captures into typed Parquet flow tables (one file per scenario/capture).

python -m botgraph_ml.prepare ctu13
python -m botgraph_ml.prepare iot23
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from botgraph_core import iter_ctu13_binetflow, iter_zeek_conn
from botgraph_ml.config import load_params, repo_path


def write_parquet(chunks: Iterator[pd.DataFrame], dest: Path) -> int:
    """Write streamed flow chunks to one Parquet file; returns the row count."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    rows = 0
    writer: pq.ParquetWriter | None = None
    try:
        for chunk in chunks:
            table = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(tmp, table.schema, compression="zstd")
            writer.write_table(table.cast(writer.schema))
            rows += len(chunk)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise ValueError(f"no rows to write for {dest}")
    tmp.rename(dest)
    return rows


def prepare_ctu13(raw_dir: Path, out_dir: Path) -> dict[str, int]:
    counts = {}
    for src in tqdm(sorted(raw_dir.glob("scenario=*/flows.binetflow")), desc="ctu13"):
        scenario = src.parent.name  # "scenario=N"
        counts[scenario] = write_parquet(
            iter_ctu13_binetflow(src, sensor_id=f"ctu13-{scenario}"),
            out_dir / f"{scenario}.parquet",
        )
    return counts


def prepare_iot23(raw_dir: Path, out_dir: Path) -> dict[str, int]:
    counts = {}
    for src in tqdm(sorted(raw_dir.glob("*/conn.log.labeled")), desc="iot23"):
        capture = src.parent.name
        counts[capture] = write_parquet(
            iter_zeek_conn(src, sensor_id=f"iot23-{capture}"), out_dir / f"{capture}.parquet"
        )
    return counts


PREPARERS = {"ctu13": prepare_ctu13, "iot23": prepare_iot23}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("dataset", choices=sorted(PREPARERS))
    args = parser.parse_args(argv)

    params = load_params()
    raw_dir = repo_path(params["data"]["raw_dir"]) / args.dataset
    out_dir = repo_path(params["data"]["processed_dir"]) / args.dataset / "flows"
    if not raw_dir.exists():
        raise SystemExit(
            f"{raw_dir} not found; run `python -m botgraph_ml.download {args.dataset}` first"
        )

    counts = PREPARERS[args.dataset](raw_dir, out_dir)
    for name, n in counts.items():
        print(f"{name}: {n:,} flows")


if __name__ == "__main__":
    main()
