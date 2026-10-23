"""Reopening a stage must not leave an active application on a closed order."""
from app.db.models import OrderStatus
from app.domain.orders import reopen_blocker


def test_closed_order_blocks_stage_reopen():
    assert "closed" in reopen_blocker(OrderStatus.closed)
    assert reopen_blocker("closed")


def test_other_order_states_allow_stage_reopen():
    for state in (OrderStatus.open, OrderStatus.on_hold, OrderStatus.filled):
        assert reopen_blocker(state) is None
