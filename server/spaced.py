"""SM-2 spaced repetition (the algorithm behind Anki)."""
from datetime import date, timedelta

from . import vault


def next_state(ease, interval, reps, quality):
    """quality: 0-5. Returns (ease, interval_days, reps)."""
    if quality < 3:
        reps, interval = 0, 1
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
    ease = max(1.3, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    return ease, interval, reps


def add_cards(item_id, output_id, cards):
    today = date.today().isoformat()
    import time
    now = time.time()
    vault.many(
        "INSERT INTO cards(item_id,output_id,front,back,due,created_at) VALUES(?,?,?,?,?,?)",
        [(item_id, output_id, c["front"], c["back"], today, now) for c in cards if c.get("front") and c.get("back")],
    )


def due_cards(item_id=None, limit=50):
    today = date.today().isoformat()
    sql = ("SELECT c.*, i.title FROM cards c JOIN items i ON i.id=c.item_id WHERE c.due<=?")
    args = [today]
    if item_id:
        sql += " AND c.item_id=?"
        args.append(item_id)
    sql += " ORDER BY c.due, c.id LIMIT ?"
    args.append(limit)
    return vault.rows(sql, tuple(args))


def review(card_id, quality):
    card = vault.one("SELECT * FROM cards WHERE id=?", (card_id,))
    if not card:
        return None
    ease, interval, reps = next_state(card["ease"], card["interval"], card["reps"], quality)
    due = (date.today() + timedelta(days=interval)).isoformat()
    vault.run("UPDATE cards SET ease=?,interval=?,reps=?,due=? WHERE id=?", (ease, interval, reps, due, card_id))
    vault.log_event("review", card["item_id"])
    return {"id": card_id, "ease": ease, "interval": interval, "reps": reps, "due": due}
