"""ASCII-Parser: Integra 8S liefert teils leere Werte (z. B. abschließendes '#')."""
import importlib.util
import pathlib
import sys

import pytest

_PATH = pathlib.Path(__file__).parents[1] / "custom_components/work_integra/integra.py"
_spec = importlib.util.spec_from_file_location("integra_ascii", _PATH)
ia = importlib.util.module_from_spec(_spec)
sys.modules["integra_ascii"] = ia
_spec.loader.exec_module(ia)


def test_empty_values_are_skipped():
    s, _e, values = ia.IntegraClient._parse_values("output#gain#0-7#0.0#-1.5#0#0#0#0#0#0#")
    result = ia.IntegraClient._floats(s, values)
    assert result[1] == -1.5 and len(result) == 8


def test_reply_without_values_raises():
    with pytest.raises(ia.IntegraError):
        ia.IntegraClient._floats(0, ["", ""])
