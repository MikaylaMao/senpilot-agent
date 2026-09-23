from pathlib import Path
from zipfile import ZipFile

import pytest

from agent import Matter, _matter_details, package, parse_request, reply_text


def test_request_parsing():
    assert parse_request("M12205", "Please send Other Documents") == ("M12205", "Other Documents")
    with pytest.raises(ValueError):
        parse_request("M12205 and M12383", "Exhibits")
    with pytest.raises(ValueError):
        parse_request("M12205", "all files")


def test_zip_and_summary(tmp_path: Path):
    document = tmp_path / "01_example.pdf"
    document.write_bytes(b"%PDF-1.4\n")
    output = tmp_path / "documents.zip"
    package([document], output)
    with ZipFile(output) as archive:
        assert archive.namelist() == [document.name]
        assert archive.read(document.name) == document.read_bytes()
    matter = Matter("M12205", "Windsor Street Exchange", {"Category": "Capital Expenditure"}, {"Exhibits": 13, "Other Documents": 21})
    text = reply_text(matter, "Other Documents", 10)
    assert "34 files" in text and "10 of 21 Other Documents" in text


def test_matter_header_from_filemaker():
    header = """Exhibits - 13
Key Documents - 6
Other Documents - 43
M12205
Capital Expenditure Approvals
Open
Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000
04/07/2025
10/23/2025
Water
Back to Search Results"""
    title, metadata = _matter_details(header, "M12205")
    assert title.startswith("Halifax Regional Water Commission")
    assert metadata == {
        "Type": "Water", "Category": "Capital Expenditure Approvals",
        "Status": "Open", "Date Received": "04/07/2025",
        "Decision Date": "10/23/2025",
    }
