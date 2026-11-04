"""OIG LEIE possible matches. A SYNTHETIC CSV only (gotcha 8): no real exclusion rows."""

import asyncio
import os
import time

import httpx
import pytest

from app.sources import leie
from tests.fakes import sources_fixture  # noqa: F401 - fixture

HEADER = ("LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,"
          "EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE\n")
ROWS = """\
RIVERA,ALEX,J,,IND- LIC HC SERV PRO,NURSING,U0001,0000000001,19700101,1 SYNTHETIC ST,TESTVILLE,OH,00001,1128a1,20200115,00000000,00000000,
RIVERA,ALEX,,,IND- LIC HC SERV PRO,PHARMACY,,0,19710202,2 SYNTHETIC ST,TESTVILLE,TX,00002,1128b4,20210220,00000000,00000000,
RIVERA,ALEX,Q,,IND- LIC HC SERV PRO,DENTIST,,0,19720303,3 SYNTHETIC ST,TESTVILLE,CA,00003,1128b4,20220325,00000000,00000000,
EXAMPLE,DANA,,,IND- LIC HC SERV PRO,NURSING,,0,19730404,4 SYNTHETIC ST,TESTVILLE,NY,00004,1128a1,20230430,00000000,00000000,
RIVERA,ALEXANDRA,,,IND- LIC HC SERV PRO,NURSING,,0,19740505,5 SYNTHETIC ST,TESTVILLE,FL,00005,1128a1,20240505,00000000,00000000,
,,,SYNTHETIC CLINIC LLC,BUSINESS,CLINIC,,0,,6 SYNTHETIC ST,TESTVILLE,WA,00006,1128b5,20190101,00000000,00000000,
"""
CSV = (HEADER + ROWS).encode()


def write_csv(age_days=0):
    path = leie.csv_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CSV)
    then = time.time() - age_days * 86400
    os.utime(path, (then, then))
    return path


def test_exact_name_match_keeps_allowed_columns_only():
    write_csv()
    matches = asyncio.run(leie.possible_matches("Álex", "rivera"))
    assert [m["specialty"] for m in matches] == ["NURSING", "PHARMACY", "DENTIST"]  # not ALEXANDRA
    assert set(matches[0]) == {"general", "specialty", "excltype", "excldate", "reindate", "state", "url"}
    assert matches[0] == {"general": "IND- LIC HC SERV PRO", "specialty": "NURSING", "excltype": "1128a1",
                          "excldate": "20200115", "reindate": "00000000", "state": "OH",
                          "url": "https://exclusions.oig.hhs.gov/"}
    text = repr(matches)
    for private in ("1970", "SYNTHETIC ST", "TESTVILLE", "00001", "U0001", "0000000001"):
        assert private not in text


def test_middle_initial_keeps_blank_or_same_initial():
    write_csv()
    matches = asyncio.run(leie.possible_matches("Alex", "Rivera", middle_initial="J."))
    assert [m["state"] for m in matches] == ["OH", "TX"]


def test_one_name_part_never_matches():
    write_csv()
    assert asyncio.run(leie.possible_matches("", "Rivera")) == []
    assert asyncio.run(leie.possible_matches("", "")) == []  # business rows have empty names


def test_fresh_copy_is_not_downloaded_again(sources):
    write_csv(age_days=10)
    _, seen = sources()
    assert len(asyncio.run(leie.possible_matches("Dana", "Example"))) == 1
    assert seen == []


def test_missing_or_old_copy_is_downloaded(sources):
    _, seen = sources(routes={"oig.hhs.gov": lambda request: httpx.Response(200, content=CSV)})
    assert len(asyncio.run(leie.possible_matches("Dana", "Example"))) == 1
    assert str(seen[0].url) == leie.CSV_URL
    assert leie.csv_path().read_bytes() == CSV


def test_failed_download_uses_old_copy_or_raises(sources):
    sources(routes={"oig.hhs.gov": (503, {})})
    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(leie.possible_matches("Dana", "Example"))
    write_csv(age_days=60)
    assert len(asyncio.run(leie.possible_matches("Dana", "Example"))) == 1


def test_error_page_is_not_saved(sources):
    sources(routes={"oig.hhs.gov": lambda request: httpx.Response(200, content=b"<html>maintenance</html>")})
    with pytest.raises(RuntimeError, match="expected CSV"):
        asyncio.run(leie.possible_matches("Dana", "Example"))
    assert not leie.csv_path().exists()
