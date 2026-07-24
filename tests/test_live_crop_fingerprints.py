from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "build_book_catalog.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("build_book_catalog", MODULE_PATH)
assert SPEC and SPEC.loader
catalog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = catalog
SPEC.loader.exec_module(catalog)


def test_persistent_fingerprint_reuses_only_near_identical_crop(tmp_path: Path) -> None:
    con = catalog.open_fingerprint_db(tmp_path / "fingerprints.db")
    assert con is not None
    original = int("aa55aa55aa55aa55", 16)
    titles = [{"title": "Go言語プログラミング"}]
    catalog.save_persistent_crop(con, "shelf-A:1:2:3:4", original, "job-1", "crop-1", titles)

    same = catalog.find_persistent_crop(con, "shelf-A:1:2:3:4", original ^ 0b11)
    assert same and same["titles"] == titles
    assert catalog.find_persistent_crop(con, "shelf-A:1:2:3:4", original ^ 0xFFFF) is None
    assert catalog.find_persistent_crop(con, "shelf-B:1:2:3:4", original) is None
    con.close()
