"""Your custom screener rules: `field op value`, either Required (a filter) or worth +/- points (ranking).
Saved to screener_rules.json in the app data folder."""
import json
import operator

from .config import DATA

RULES_FILE = DATA / "screener_rules.json"

# Every field a rule can use. 0/1 fields are yes/no.
FIELDS = {
    "price": "Price ($)",
    "change_pct": "Change vs prior close (%)",
    "gap_atr": "Gap (daily ATRs)",
    "rvol": "Relative volume (x)",
    "avg_usd_vol_m": "Avg $ volume (millions)",
    "spread_pct": "Spread (%)",
    "rsi_5m": "RSI 14 on 5-min",
    "vwap_dist_pct": "Distance from VWAP (%)",
    "above_ema9": "Above EMA 9 on 5-min (1 = yes)",
    "ema_stack": "Price > EMA 9 > EMA 20 on 5-min (1 = yes)",
    "pct_from_open": "Change from today's open (%)",
    "pct_from_hod": "Below high of day (%, 0 = at the high)",
    "range_pos": "Position in day's range (0 = low, 100 = high)",
    "has_catalyst": "Has a tagged news catalyst today (1 = yes)",
    # ATR-scaled (daily ATR 14): one threshold works across cheap and expensive stocks.
    "vwap_dist_atr": "Distance from VWAP (ATRs)",
    "hod_dist_atr": "Below high of day (ATRs)",
    "chg_open_atr": "Change from today's open (ATRs)",
    "or5_width_atr": "5-min opening range width (ATRs)",
    "bar_vol_ratio": "Last 5-min bar volume vs 20-bar average (x)",
    "mins_since_open": "Minutes since 09:30",
}
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "=": operator.eq, "!=": operator.ne}


def validate(rules: list[dict]) -> list[dict]:
    out = []
    for r in rules:
        if r.get("field") not in FIELDS:
            raise ValueError(f"Unknown field {r.get('field')!r}")
        if r.get("op") not in OPS:
            raise ValueError(f"Unknown operator {r.get('op')!r}")
        if r.get("action") not in ("require", "score"):
            raise ValueError("action must be 'require' or 'score'")
        value, points = float(r["value"]), float(r.get("points") or 0)
        if not -100 <= points <= 100:
            raise ValueError("points must be between -100 and 100")
        out.append({"field": r["field"], "op": r["op"], "value": value, "action": r["action"],
                    "points": points if r["action"] == "score" else 0.0, "on": bool(r.get("on", True))})
    return out


def load() -> list[dict]:
    return json.loads(RULES_FILE.read_text()) if RULES_FILE.exists() else []


def save(rules: list[dict]) -> list[dict]:
    rules = validate(rules)
    RULES_FILE.write_text(json.dumps(rules, indent=1))
    return rules


def label(r: dict) -> str:
    v = r["value"]
    return f"{r['field']} {r['op']} {int(v) if v == int(v) else v}"


def apply(f: dict, rules: list[dict]) -> tuple[list[str], float, list[str]]:
    """-> (failed required rules, total points, matched score rules). A field without data fails a required
    rule and earns no points."""
    fails, points, hits = [], 0.0, []
    for r in rules:
        if not r.get("on", True):
            continue
        v = f.get(r["field"])
        ok = v is not None and OPS[r["op"]](v, r["value"])
        if r["action"] == "require" and not ok:
            fails.append(f"rule: {label(r)}" + ("" if v is not None else " (no data)"))
        elif r["action"] == "score" and ok:
            points += r["points"]
            hits.append(f"{label(r)} {r['points']:+g}")
    return fails, round(points, 1), hits
