from types import SimpleNamespace as NS

from app.orders import plan_exits
from app.portfolio import fill_avg


def test_fill_avg():
    assert fill_avg([("BOT", 100, 10.0)], 100) == 10.0
    assert fill_avg([("BOT", 100, 10.0), ("BOT", 100, 11.0)], 200) == 10.5           # add re-averages
    assert fill_avg([("BOT", 100, 10.0), ("BOT", 100, 11.0), ("SLD", 50, 12.0)], 150) == 10.5  # reduce keeps avg
    assert fill_avg([("BOT", 100, 10.0), ("SLD", 100, 11.0), ("BOT", 10, 9.0)], 10) == 9.0     # flat, then new
    assert fill_avg([("SLD", 50, 20.0), ("SLD", 50, 19.0)], -100) == 19.5           # short
    assert fill_avg([("BOT", 100, 10.0), ("SLD", 150, 11.0)], -50) == 11.0          # flip long -> short
    assert fill_avg([("BOT", 10, 10.0)], 73) is None                                 # held from before: unknown


def trade(oid, ref, action, typ, qty, parent=0, oca="", filled=0, status="PreSubmitted"):
    return NS(order=NS(orderId=oid, orderRef=ref, action=action, orderType=typ, totalQuantity=qty, parentId=parent,
                       ocaGroup=oca), orderStatus=NS(filled=filled, status=status), contract=NS(conId=1, symbol="X"))


def bracket(ref, entry_id, qty, entry_status=None, entry_filled=0):
    out = [trade(entry_id + 1, ref, "SELL", "STP", qty, entry_id, f"{ref}-exits"),
           trade(entry_id + 2, ref, "SELL", "MIT", qty, entry_id, f"{ref}-exits")]
    if entry_status:  # entry still working
        out.append(trade(entry_id, ref, "BUY", "MKT", qty, status=entry_status, filled=entry_filled))
    return out


def acts(plan):
    return [(a, t.order.orderRef, t.order.orderType, q) for a, t, q in plan]


def test_single_bracket_matching_position_untouched():
    assert plan_exits(bracket("TA3", 10, 100), 100) == []


def test_add_merges_into_newest_bracket():
    # Held 100 (TA3), bought 28 more (TA6): one stop/target, the newest, for all 128 shares.
    plan = acts(plan_exits(bracket("TA3", 10, 100) + bracket("TA6", 20, 28), 128))
    assert plan == [("cancel", "TA3", "STP", 0), ("cancel", "TA3", "MIT", 0),
                    ("modify", "TA6", "STP", 128), ("modify", "TA6", "MIT", 128)]


def test_sold_part_elsewhere_shrinks_exits():
    assert acts(plan_exits(bracket("TA7", 30, 100), 60)) == [("modify", "TA7", "STP", 60), ("modify", "TA7", "MIT", 60)]


def test_flat_or_short_cancels_all_exits():
    assert [a for a, *_ in acts(plan_exits(bracket("TA7", 30, 100), 0))] == ["cancel", "cancel"]
    assert [a for a, *_ in acts(plan_exits(bracket("TA7", 30, 100), -5))] == ["cancel", "cancel"]


def test_partly_filled_target_counts_its_fills():
    b = bracket("TA7", 30, 100)
    b[1].orderStatus.filled = 40      # target sold 40; IBKR (OCA type 2) shrinks the stop; position is 60
    b[0].order.totalQuantity = 60
    assert plan_exits(b, 60) == []    # target total 100 = 40 filled + 60 left: consistent


def test_newest_by_number_not_text():
    plan = acts(plan_exits(bracket("TA10", 50, 1) + bracket("TA9", 60, 1), 2))
    assert ("cancel", "TA9", "STP", 0) in plan and ("modify", "TA10", "STP", 2) in plan


def test_working_entry_left_alone_and_partfill_waits():
    assert plan_exits(bracket("TA8", 40, 100, entry_status="Submitted"), 0) == []
    assert plan_exits(bracket("TA3", 10, 50) + bracket("TA8", 40, 100, "Submitted", entry_filled=30), 80) == []
