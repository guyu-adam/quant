"""快照写入、稳定性与完整性校验测试。"""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from q6.data.snapshot import SnapshotIntegrityError, load_snapshot, verify_snapshot, write_snapshot


def _tables() -> dict[str, pd.DataFrame]:
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2012-01-03", "2010-01-04", "2011-01-04"]),
            "code": ["sh.600001", "sh.600002", "sh.600003"],
            "close": [3.0, 1.0, 2.0],
            "volume": [30, 10, 20],
            "tradestatus": [1, 1, 0],
            "isST": [0, 0, 1],
            "bad": [False, False, True],
            "ret": [0.1, 0.0, None],
        }
    )
    return {
        "daily": daily,
        "quarantine": pd.DataFrame(
            {"code": ["sh.600003"], "date": pd.to_datetime(["2011-01-04"]), "reason": ["停牌"]}
        ),
        "calendar": pd.DataFrame({"date": pd.to_datetime(["2010-01-04", "2011-01-04", "2012-01-03"])}),
    }


def _expected(tables: dict[str, pd.DataFrame], key: str) -> pd.DataFrame:
    frame = tables[key].copy()
    for column in ("date", "month_end", "update_date"):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column]).astype("datetime64[ns]")
    for column in ("tradestatus", "isST"):
        if column in frame:
            frame[column] = frame[column].astype("int8")
    if "volume" in frame:
        frame["volume"] = frame["volume"].astype("int64")
    if "code" in frame:
        frame["code"] = frame["code"].astype(str)
    for column in frame.columns:
        if (
            column not in {"date", "month_end", "code", "volume", "tradestatus", "isST"}
            and pd.api.types.is_numeric_dtype(frame[column])
            and not pd.api.types.is_bool_dtype(frame[column])
        ):
            frame[column] = frame[column].astype("float64")
    sort_cols = ["date", "code"] if key == "daily" else list(frame.columns)
    return frame.sort_values(sort_cols, kind="mergesort", na_position="first").reset_index(drop=True)


def test_write_is_content_addressed_and_parquet_bytes_are_stable(tmp_path: Path) -> None:
    tables = _tables()
    first_root, second_root = tmp_path / "one", tmp_path / "two"
    first_id = write_snapshot(tables, first_root, asof="2012-12-31", sources={"feed": "a"}, code_version="v1")
    second_id = write_snapshot(
        tables, second_root, asof="2012-12-31", sources={"feed": "b"}, code_version="v2"
    )

    assert first_id == second_id
    first_manifest = json.loads((first_root / first_id / "manifest.json").read_text())
    second_manifest = json.loads((second_root / second_id / "manifest.json").read_text())
    first_manifest.pop("created_at")
    second_manifest.pop("created_at")
    assert first_manifest["sources"] != second_manifest["sources"]
    first_manifest.pop("sources")
    second_manifest.pop("sources")
    first_manifest.pop("code_version")
    second_manifest.pop("code_version")
    assert first_manifest == second_manifest
    for entry in first_manifest["files"]:
        one = first_root / first_id / entry["path"]
        two = second_root / second_id / entry["path"]
        assert one.read_bytes() == two.read_bytes()
        assert hashlib.sha256(one.read_bytes()).hexdigest() == entry["sha256"]

    loaded = load_snapshot(first_root, first_id, tables=("daily", "quarantine", "calendar"))
    for key in loaded:
        pd.testing.assert_frame_equal(loaded[key], _expected(tables, key))


def test_load_detects_changed_parquet_byte(tmp_path: Path) -> None:
    snapshot_id = write_snapshot(_tables(), tmp_path, asof="2012-12-31", sources={}, code_version="test")
    path = tmp_path / snapshot_id / "daily/year=2010.parquet"
    content = bytearray(path.read_bytes())
    content[len(content) // 2] ^= 1
    path.write_bytes(content)
    with pytest.raises(SnapshotIntegrityError, match="year=2010.parquet.*SHA256"):
        load_snapshot(tmp_path, snapshot_id, years=(2010, 2010))


def test_verify_detects_manifest_row_count_change(tmp_path: Path) -> None:
    snapshot_id = write_snapshot(_tables(), tmp_path, asof="2012-12-31", sources={}, code_version="test")
    manifest_path = tmp_path / snapshot_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["rows"] += 1
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SnapshotIntegrityError, match="快照 ID 不一致"):
        verify_snapshot(tmp_path, snapshot_id)


def test_verify_rejects_unlisted_parquet(tmp_path: Path) -> None:
    snapshot_id = write_snapshot(_tables(), tmp_path, asof="2012-12-31", sources={}, code_version="test")
    (tmp_path / snapshot_id / "extra.parquet").write_bytes(b"extra")
    with pytest.raises(SnapshotIntegrityError, match="清单外.*extra.parquet"):
        verify_snapshot(tmp_path, snapshot_id)


def test_year_range_reads_only_requested_partitions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_id = write_snapshot(_tables(), tmp_path, asof="2012-12-31", sources={}, code_version="test")
    import q6.data.snapshot as snapshot_module

    read_paths = []
    original = snapshot_module._validate_file

    def track(directory: Path, entry: dict) -> Path:
        read_paths.append(entry["path"])
        return original(directory, entry)

    monkeypatch.setattr(snapshot_module, "_validate_file", track)
    loaded = load_snapshot(tmp_path, snapshot_id, years=(2010, 2012))
    assert set(read_paths) == {
        "daily/year=2010.parquet",
        "daily/year=2011.parquet",
        "daily/year=2012.parquet",
    }
    assert set(loaded["daily"]["date"].dt.year) == {2010, 2011, 2012}


def test_columns_prunes_daily_but_keeps_keys_and_still_verifies_hash(tmp_path: Path) -> None:
    snapshot_id = write_snapshot(_tables(), tmp_path, asof="2012-12-31", sources={}, code_version="test")
    full = load_snapshot(tmp_path, snapshot_id)["daily"]
    pruned = load_snapshot(tmp_path, snapshot_id, columns=("close",))["daily"]
    assert list(pruned.columns) == ["date", "code", "close"]
    pd.testing.assert_frame_equal(pruned, full[["date", "code", "close"]])

    path = tmp_path / snapshot_id / "daily" / "year=2010.parquet"
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(SnapshotIntegrityError):
        load_snapshot(tmp_path, snapshot_id, columns=("close",))


def test_universe_monthly_round_trip_keeps_string_index_and_normalizes_update_date(
    tmp_path: Path,
) -> None:
    tables = _tables()
    universe = pd.DataFrame(
        {
            "month_end": ["2020-01-31", "2020-02-28"],
            "code": ["sh.600001", "sh.600002"],
            "index": ["hs300", "zz500"],
            "update_date": ["2020-02-01", "2020-03-01"],
        }
    )
    tables["universe_monthly"] = universe

    snapshot_id = write_snapshot(tables, tmp_path, asof="2020-03-01", sources={}, code_version="test")
    loaded = load_snapshot(tmp_path, snapshot_id, tables=("universe_monthly",))

    expected = universe.copy()
    expected["month_end"] = pd.to_datetime(expected["month_end"]).astype("datetime64[ns]")
    expected["update_date"] = pd.to_datetime(expected["update_date"]).astype("datetime64[ns]")
    expected["code"] = expected["code"].astype(str)
    expected = expected.sort_values(list(expected.columns), kind="mergesort").reset_index(drop=True)
    pd.testing.assert_frame_equal(loaded["universe_monthly"], expected)
    assert loaded["universe_monthly"]["index"].tolist() == ["hs300", "zz500"]


def test_load_missing_table_raises_key_error_with_table_name(tmp_path: Path) -> None:
    snapshot_id = write_snapshot(_tables(), tmp_path, asof="2012-12-31", sources={}, code_version="test")

    with pytest.raises(KeyError, match="universe_monthly"):
        load_snapshot(tmp_path, snapshot_id, tables=("universe_monthly",))


def test_yearly_iterator_matches_full_frame_snapshot_bytes(tmp_path: Path) -> None:
    tables = _tables()
    full_root, streamed_root = tmp_path / "full", tmp_path / "streamed"
    full_id = write_snapshot(tables, full_root, asof="2012-12-31", sources={}, code_version="same")
    daily = tables["daily"]
    yearly = ((int(year), group) for year, group in daily.groupby(daily["date"].dt.year, sort=True))
    streamed_id = write_snapshot(
        {**tables, "daily": yearly},
        streamed_root,
        asof="2012-12-31",
        sources={},
        code_version="same",
    )
    assert full_id == streamed_id
    manifest = json.loads((full_root / full_id / "manifest.json").read_text())
    for entry in manifest["files"]:
        assert (full_root / full_id / entry["path"]).read_bytes() == (
            streamed_root / streamed_id / entry["path"]
        ).read_bytes()


def test_snapshot_keeps_missing_volume_as_nullable_integer(tmp_path: Path) -> None:
    tables = _tables()
    tables["daily"].loc[0, "volume"] = None
    snapshot_id = write_snapshot(tables, tmp_path, asof="2012-12-31", sources={}, code_version="test")
    daily = load_snapshot(tmp_path, snapshot_id)["daily"]
    row = daily.loc[daily["code"] == "sh.600001"].iloc[0]
    assert pd.isna(row["volume"])


def test_year_partition_path_iterator_preserves_snapshot_bytes(tmp_path: Path) -> None:
    tables = _tables()
    source_root, copied_root = tmp_path / "source", tmp_path / "copied"
    snapshot_id = write_snapshot(tables, source_root, asof="2012-12-31", sources={}, code_version="same")
    manifest = json.loads((source_root / snapshot_id / "manifest.json").read_text())
    partitions = [
        (int(Path(item["path"]).stem.split("=")[1]), source_root / snapshot_id / item["path"])
        for item in manifest["files"]
        if item["path"].startswith("daily/")
    ]
    copied_id = write_snapshot(
        {**tables, "daily": iter(partitions)},
        copied_root,
        asof="2012-12-31",
        sources={},
        code_version="same",
    )
    assert copied_id == snapshot_id
    for _, path in partitions:
        assert path.read_bytes() == (copied_root / copied_id / "daily" / path.name).read_bytes()
