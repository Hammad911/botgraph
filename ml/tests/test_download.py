from __future__ import annotations

import io
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from botgraph_ml.download import ctu13_target, extract_matching, iot23_target


def _make_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:bz2") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_ctu13_extraction_layout(tmp_path: Path) -> None:
    archive = tmp_path / "ctu13.tar.bz2"
    _make_tar(
        archive,
        {
            "CTU-13-Dataset/3/capture20110812.binetflow": b"flows-3",
            "CTU-13-Dataset/10/capture20110818.binetflow": b"flows-10",
            "CTU-13-Dataset/3/botnet-capture-20110812-rbot.pcap": b"pcap",
            "CTU-13-Dataset/README": b"readme",
        },
    )
    written = extract_matching(archive, tmp_path / "out", ctu13_target)

    assert sorted(p.relative_to(tmp_path / "out").as_posix() for p in written) == [
        "scenario=10/flows.binetflow",
        "scenario=3/flows.binetflow",
    ]
    assert (tmp_path / "out/scenario=3/flows.binetflow").read_bytes() == b"flows-3"


def test_archive_paths_cannot_escape_output_dir(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.bz2"
    _make_tar(archive, {"../../7/evil.binetflow": b"x"})
    written = extract_matching(archive, tmp_path / "out", ctu13_target)
    assert [p.relative_to(tmp_path / "out").as_posix() for p in written] == [
        "scenario=7/flows.binetflow"
    ]


def test_iot23_target() -> None:
    path = PurePosixPath(
        "opt/Malware-Project/BigDataset/IoTScenarios/CTU-IoT-Malware-Capture-34-1/bro/conn.log.labeled"
    )
    assert iot23_target(path) == Path("CTU-IoT-Malware-Capture-34-1/conn.log.labeled")
    assert iot23_target(PurePosixPath("x/bro/dns.log")) is None


def test_download_retries_network_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    from botgraph_ml import download as dl

    calls = {"n": 0}

    def flaky(url: str, dest: Path) -> Path:
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("Read timed out.")
        dest.write_text("ok")
        return dest

    monkeypatch.setattr(dl, "_download_once", flaky)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    out = dl.download("https://example.invalid/f", tmp_path / "f", attempts=5)
    assert out.read_text() == "ok" and calls["n"] == 3


def test_download_gives_up_after_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    from botgraph_ml import download as dl

    def always_down(url: str, dest: Path) -> Path:
        raise requests.Timeout("down")

    monkeypatch.setattr(dl, "_download_once", always_down)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    with pytest.raises(requests.Timeout):
        dl.download("https://example.invalid/f", tmp_path / "f", attempts=3)
