from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from botgraph_core.adapters import read_ctu13_binetflow, read_zeek_conn

CTU13_SAMPLE = """\
StartTime,Dur,Proto,SrcAddr,Sport,Dir,DstAddr,Dport,State,sTos,dTos,TotPkts,TotBytes,SrcBytes,Label
2011/08/10 09:46:53.047277,3.124,tcp,147.32.84.165,1027,   ->,74.125.232.195,80,SRPA_SPA,0,0,12,1066,588,flow=From-Botnet-V42-TCP-HTTP-Google-Net-Established-6
2011/08/10 09:46:54.000000,0.000,icmp,147.32.84.165,0x0008,   ->,147.32.96.69,0x0303,ECO,0,,1,70,70,flow=Background
2011/08/10 09:46:55.500000,1.0,udp,147.32.84.170,53,  <->,147.32.80.9,53,CON,0,0,2,200,80,flow=To-Normal-V42-UDP-CVUT-DNS-Server
2011/08/10 09:46:56.000000,0.0,arp,00:15:17:2c:e5:2d,,  who,00:21:9b:2e:4f:10,,INT,0,0,1,60,60,flow=Background
"""

ZEEK_SAMPLE = (
    "#separator \\x09\n"
    "#fields\tts\tuid\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice\tduration"
    "\torig_bytes\tresp_bytes\tconn_state\tlocal_orig\tlocal_resp\tmissed_bytes\thistory"
    "\torig_pkts\torig_ip_bytes\tresp_pkts\tresp_ip_bytes\ttunnel_parents   label   detailed-label\n"
    "1525879831.015811\tC1\t192.168.100.103\t51524\t65.127.233.163\t23\ttcp\t-\t2.999051"
    "\t0\t0\tS0\t-\t-\t0\tS\t3\t180\t0\t0\t(empty)   Malicious   PartOfAHorizontalPortScan\n"
    "1525879832.025055\tC2\t192.168.100.103\t56305\t63.150.16.171\t80\ttcp\thttp\t-"
    "\t-\t-\tSF\t-\t-\t0\tShADadFf\t5\t300\t4\t900\t(empty)   Benign   -\n"
)


def test_ctu13_binetflow() -> None:
    df = read_ctu13_binetflow(io.StringIO(CTU13_SAMPLE))

    assert len(df) == 3  # the ARP row (MAC addresses) is dropped
    assert df["label"].tolist() == ["botnet", "unknown", "benign"]
    assert df["proto"].tolist() == ["tcp", "icmp", "udp"]
    assert df.loc[0, "dst_bytes"] == 1066 - 588
    assert df.loc[1, "dst_port"] == 0x0303  # Argus encodes ICMP type/code as hex
    expected = datetime(2011, 8, 10, 9, 46, 53, 47277, tzinfo=UTC).timestamp()
    assert df.loc[0, "ts"] == pytest.approx(expected)


def test_iot23_zeek_conn(tmp_path: Path) -> None:
    path = tmp_path / "conn.log.labeled"
    path.write_text(ZEEK_SAMPLE)
    df = read_zeek_conn(path)

    assert df["label"].tolist() == ["botnet", "benign"]
    assert df["pkts"].tolist() == [3, 9]
    assert df.loc[1, "duration"] == 0.0  # "-" means missing in Zeek
    assert df.loc[0, "dst_port"] == 23
