from types import SimpleNamespace as NS

from app.orders import plan_exit_sizes
from app.portfolio import fill_avg


def test_fill_avg():
    assert fill_avg([("BOT", 100, 10.0)], 100) == 10.0
    assert fill_avg([("BOT", 100, 10.0), ("BOT", 100, 11.0)], 200) == 10.5           # add re-averages
    assert fill_avg([("BOT", 100, 10.0), ("BOT", 100, 11.0), ("SLD", 50, 12.0)], 150) == 10.5  # reduce keeps avg
    assert fill_avg([("BOT", 100, 10.0), ("SLD", 100, 11.0), ("BOT", 10, 9.0)], 10) == 9.0     # flat, then new
    assert fill_avg([("SLD", 50, 20.0), ("SLD", 50, 19.0)], -100) == 19.5           # short
    assert fill_avg([("BOT", 100, 10.0), ("SLD", 150, 11.0)], -50) == 11.0          # flip long -> short
    assert fill_avg([("BOT", 10, 10.0)], 73) is None                                 # held from before: unknown


def trade(oid, ref, action, typ, qty, parent=0, oca="", filled=0, status="PreSubmitted", con=1):
    return NS(order=NS(orderId=oid, orderRef=ref, action=action, orderType=typ, totalQuantity=qty, parentId=parent,
                       ocaGroup=oca), orderStatus=NS(filled=filled, status=status), contract=NS(conId=con, symbol="X"))


def bracket(ref, entry_id, qty, entry_status=None):
    out = [trade(entry_id + 1, ref, "SELL", "STP", qty, entry_id, f"{ref}-exits"),
           trade(entry_id + 2, ref, "SELL", "LMT", qty, entry_id, f"{ref}-exits")]
    if entry_status:  # entry still working
        out.append(trade(entry_id, ref, "BUY", "LMT", qty, status=entry_status))
    return out


def test_exits_fit_position():
    a, b = bracket("TA3", 10, 1), bracket("TA5", 20, 1)
    assert plan_exit_sizes(a + b, {1: 2}) == {}                       # 2 shares, two 1-share brackets: fine
    plan = plan_exit_sizes(a + b, {1: 1})                              # sold 1 elsewhere: newest bracket gives way
    assert [(t.order.orderRef, q) for t, q in plan[1]] == [("TA5", 0), ("TA5", 0)]
    plan = plan_exit_sizes(bracket("TA7", 30, 100), {1: 60})           # sold 40 of 100 elsewhere
    assert [q for _, q in plan[1]] == [60, 60]
    plan = plan_exit_sizes(bracket("TA7", 30, 100), {})                # position gone: cancel exits
    assert [q for _, q in plan[1]] == [0, 0]


def test_oldest_bracket_kept_numerically():
    plan = plan_exit_sizes(bracket("TA10", 50, 1) + bracket("TA9", 60, 1), {1: 1})
    assert {t.order.orderRef for t, _ in plan[1]} == {"TA10"}


def test_exits_of_unfilled_entry_untouched():
    assert plan_exit_sizes(bracket("TA8", 40, 100, entry_status="Submitted"), {}) == {}
