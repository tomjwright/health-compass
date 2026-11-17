from health_compass.ingest.schema_check import compare, flatten

METADATA = [
    {
        "name": "protocolSection",
        "sourceType": "STRUCT",
        "type": "ProtocolSection",
        "children": [{"name": "nctId", "sourceType": "TEXT", "type": "nct"}],
    }
]


def test_flatten_gives_dotted_paths_with_types() -> None:
    assert flatten(METADATA) == {
        "protocolSection": "STRUCT:ProtocolSection",
        "protocolSection.nctId": "TEXT:nct",
    }


def test_new_field_is_not_breaking() -> None:
    diff = compare({"a": "TEXT:text"}, {"a": "TEXT:text", "b": "DATE:date"})
    assert diff.added == ["b"]
    assert not diff.is_breaking


def test_removed_or_retyped_field_is_breaking() -> None:
    diff = compare({"a": "TEXT:text", "b": "DATE:date"}, {"a": "NUMERIC:int"})
    assert diff.removed == ["b"]
    assert diff.changed == ["a"]
    assert diff.is_breaking
