"""Smoke tests: collectors must run and return the standard envelope on any host."""

from __future__ import annotations

from hwprobe_mcp import aggregate
from hwprobe_mcp.collectors import lmsensors, nvidia


def _assert_envelope(resp: dict) -> None:
    assert set(resp) >= {"ok", "platform", "sources", "warnings", "data"}
    assert isinstance(resp["warnings"], list)
    assert isinstance(resp["sources"], list)


def test_hardware_inventory():
    resp = aggregate.hardware_inventory()
    _assert_envelope(resp)
    assert resp["ok"] is True
    assert "arch" in resp["data"]
    assert "os" in resp["data"]


def test_live_sensors():
    resp = aggregate.live_sensors()
    _assert_envelope(resp)
    assert "backends" in resp["data"]


def test_cpu_status():
    resp = aggregate.cpu_status()
    _assert_envelope(resp)


def test_disk_health():
    resp = aggregate.disk_health()
    _assert_envelope(resp)


def test_system_snapshot():
    resp = aggregate.system_snapshot()
    _assert_envelope(resp)
    assert {"inventory", "sensors", "cpu", "gpu", "disk_health"}.issubset(resp["data"])


def test_lmsensors_kind_classification():
    # short "in" voltage prefix must not shadow temp/power/current
    assert lmsensors._kind("temp1_input") == "temperature"
    assert lmsensors._kind("fan2_input") == "fan"
    assert lmsensors._kind("in0_input") == "voltage"
    assert lmsensors._kind("power1_input") == "power"
    assert lmsensors._kind("curr1_input") == "current"


def test_nvidia_num_parsing():
    assert nvidia._num("42") == 42
    assert nvidia._num("3.14") == 3.14
    assert nvidia._num("[Not Supported]") is None
    assert nvidia._num("N/A") is None
    assert nvidia._num("Tesla T4") == "Tesla T4"


# --- regression tests for the "collectors never raise" hardening ---


def test_boot_survives_corrupt_epoch(monkeypatch):
    from hwprobe_mcp.collectors import psutil_col

    monkeypatch.setattr(psutil_col.psutil, "boot_time", lambda: 1e30)
    warns: list[str] = []
    result = psutil_col.boot(warns)
    assert result["boot_time_iso"] is None
    assert any("boot_time_iso" in w for w in warns)


def test_slim_lsblk_tolerates_malformed():
    from hwprobe_mcp.collectors.inventory import _slim_lsblk

    assert _slim_lsblk("notalist") == []
    assert _slim_lsblk([{"name": "sda", "children": "bad"}]) == [{"name": "sda"}]
    assert _slim_lsblk(["junk", {"name": "nvme0n1"}]) == [{"name": "nvme0n1"}]


def test_dmidecode_skips_banner_and_captures_bullets():
    from hwprobe_mcp.collectors.inventory import _parse_dmidecode

    sample = (
        "# dmidecode 3.3\nGetting SMBIOS data from sysfs.\nSMBIOS 3.3.0 present.\n\n"
        "Handle 0x0000, DMI type 0, 26 bytes\nBIOS Information\n"
        "\tVendor: Acme\n\tCharacteristics:\n\t\tPCI is supported\n\t\tUEFI is supported\n"
    )
    ents = _parse_dmidecode(sample)
    assert [e.get("_section") for e in ents] == ["BIOS Information"]
    assert ents[0]["Vendor"] == "Acme"
    assert ents[0]["Characteristics"] == ["PCI is supported", "UEFI is supported"]


def test_smart_scan_skips_non_dict(monkeypatch):
    from hwprobe_mcp.collectors import smart

    monkeypatch.setattr(
        smart, "run_json", lambda *a, **k: ({"devices": ["/dev/sda", {"name": "/dev/nvme0"}, None]}, None)
    )
    assert smart.scan([]) == ["/dev/nvme0"]


def test_lmsensors_energy_and_average(monkeypatch):
    from hwprobe_mcp.collectors import lmsensors as lm

    assert lm._kind("energy1_input") == "energy"
    monkeypatch.setattr(
        lm,
        "raw",
        lambda w: {"amdgpu": {"Adapter": "PCI", "power1": {"power1_average": 42.0}, "energy1": {"energy1_input": 7.0}}},
    )
    by = {(r["label"], r["type"]): r for r in lm.normalized([])}
    assert by[("power1", "power")]["value"] == 42.0
    assert by[("energy1", "energy")]["unit"] == "J"


def test_system_snapshot_children_are_envelopes():
    snap = aggregate.system_snapshot()["data"]
    for child in snap.values():
        assert {"ok", "platform", "sources", "warnings", "data"} <= set(child)
