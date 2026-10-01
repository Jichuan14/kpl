import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from statistical_helpers import read_decisions, read_jsonl, wilson_interval


@pytest.mark.parametrize('reader', [read_decisions, read_jsonl])
def test_readers_keep_blank_lines_order_unicode_and_reject_invalid_rows(tmp_path, reader):
    path = tmp_path / 'rows.jsonl'
    path.write_text('\n{"hero_name":"英雄"}\n\n{"selected_hero_id":2}\n', encoding='utf-8')
    assert reader(path) == [{'hero_name':'英雄'}, {'selected_hero_id':2}]
    path.write_text('{}\n[]\n')
    with pytest.raises(ValueError, match='2'): reader(path)
    path.write_text('{}\ninvalid\n')
    with pytest.raises(ValueError, match='2'): reader(path)


def test_wilson_zero_extremes_and_known_interval():
    assert wilson_interval(0, 0) == (0.0, 0.0)
    lower, upper = wilson_interval(5, 10)
    assert math.isclose(lower, 0.236593090512564, abs_tol=1e-14)
    assert math.isclose(upper, 1-lower, abs_tol=1e-14)
    assert wilson_interval(0, 10)[0] == 0
    assert math.isclose(wilson_interval(10, 10)[1], 1, abs_tol=1e-14)
