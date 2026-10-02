import pytest
from _engines_real import STRATEGIES, compare


@pytest.mark.parametrize("strategy_name", tuple(STRATEGIES))
def test_event_and_vector_match_gfc(strategy_name: str) -> None:
    compare(strategy_name, "gfc_2008")
