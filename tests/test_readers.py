"""The reader registry: which reader claims a file, and what the rest of the program sees."""
import numpy as np
import pytest

import synth
from echem_analyzer import io_palmsens, readers
from echem_analyzer.readers import Reader, UnsupportedFile


def _palmsens_csv(path):
    t = np.linspace(0, 10, 101)
    synth.write_pstrace_csv(path, [("CA: i vs t", t, 1 + 0 * t, "s", "µA")])
    return path


# ===================================================================== registry

def test_palmsens_files_are_claimed_by_the_palmsens_readers(tmp_path):
    csv = _palmsens_csv(tmp_path / "run.csv")
    t = np.linspace(0, 10, 11)
    synth.write_pssession(tmp_path / "s.pssession", [
        {"title": "step", "method_id": "ad", "curves": [("i vs t", t, 1 + 0 * t, "PalmSens.Units.Second", "PalmSens.Units.MicroAmpere")]}])
    assert readers.pick(csv).key == "csv"
    assert readers.pick(tmp_path / "s.pssession").key == "pssession"
    assert readers.load(csv).fmt == "csv" and readers.load(tmp_path / "s.pssession").fmt == "pssession"


def test_a_file_no_reader_recognises_is_unsupported_not_misread(tmp_path):
    other = tmp_path / "results.csv"
    other.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    assert readers.pick(other) is None
    with pytest.raises(UnsupportedFile):
        readers.load(other)
    assert issubclass(UnsupportedFile, ValueError)                    # older callers that catch ValueError still work
    assert readers.pick(tmp_path / "notes.docx") is None              # a suffix nobody handles


def test_a_folder_scan_lists_only_files_a_reader_recognises(tmp_path):
    _palmsens_csv(tmp_path / "b run.csv")
    _palmsens_csv(tmp_path / "a run.csv")
    (tmp_path / "results.csv").write_text("a,b\n1,2\n", encoding="utf-8")        # not an instrument file
    (tmp_path / "~$lock.csv").write_bytes(b"x")
    (tmp_path / ".hidden.csv").write_bytes(b"x")
    names = [p.name for p in readers.find_files(tmp_path)]
    assert names == ["a run.csv", "b run.csv"]
    assert [p.name for p in io_palmsens.find_data_files(tmp_path)] == names


def test_the_old_entry_points_follow_the_registry(tmp_path):
    csv = _palmsens_csv(tmp_path / "run.csv")
    assert io_palmsens.load_any(csv).curves
    assert io_palmsens.SUPPORTED_SUFFIXES == readers.suffixes()
    with pytest.raises(AttributeError):
        io_palmsens.NO_SUCH_THING                                       # noqa: B018 - the module hook only knows one name


def test_the_open_dialog_filter_lists_every_suffix():
    flt = readers.file_filter()
    for suffix in readers.suffixes():
        assert f"*{suffix}" in flt
    assert flt.endswith("All files (*)")


def test_a_reader_cannot_be_registered_twice():
    with pytest.raises(ValueError):
        readers.register(Reader("csv", "again", (".csv",), lambda head: True, lambda p: None))


def test_head_reading_survives_a_multibyte_character_cut_in_two(tmp_path):
    p = tmp_path / "u16.csv"
    p.write_bytes(("Date and time:,2026\r\n" + "µ" * 20000).encode("utf-16"))
    assert readers.pick(p).key == "csv"                               # the read stops in the middle of a UTF-16 pair
