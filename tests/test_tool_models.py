import pytest
from pydantic import ValidationError

from ynab_mcp_server.tool_models import ManageBudgetedAmountInput


def test_move_between_two_categories_is_valid():
    model = ManageBudgetedAmountInput(
        plan_id="p",
        action="move",
        amount=10_000,
        month="2026-10-01",
        from_category_id="cat-a",
        to_category_id="cat-b",
    )
    assert model.from_category_id != model.to_category_id


def test_move_within_one_category_is_rejected_before_any_write():
    # Reading the same allocation twice and writing B-amount then B+amount nets +amount.
    with pytest.raises(ValidationError, match="different"):
        ManageBudgetedAmountInput(
            plan_id="p",
            action="move",
            amount=10_000,
            month="2026-10-01",
            from_category_id="cat-a",
            to_category_id="cat-a",
        )


def test_move_needs_both_categories():
    with pytest.raises(ValidationError):
        ManageBudgetedAmountInput(
            plan_id="p", action="move", amount=1, month="2026-10-01", to_category_id="cat-b"
        )


def test_assign_rejects_a_source_category():
    with pytest.raises(ValidationError):
        ManageBudgetedAmountInput(
            plan_id="p",
            action="assign",
            amount=1,
            month="2026-10-01",
            from_category_id="x",
            to_category_id="y",
        )
