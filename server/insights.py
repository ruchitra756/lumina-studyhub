"""Progress dashboard numbers: counts, activity, quiz performance, weak areas, achievements."""
import json
import time
from collections import Counter
from datetime import date, datetime, timedelta

from . import vault


def _day(ts):
    return datetime.fromtimestamp(ts).date()


def _streak(days):
    s, d = 0, date.today()
    if d not in days:
        d -= timedelta(days=1)
    while d in days:
        s += 1
        d -= timedelta(days=1)
    return s


def progress():
    items = vault.one("SELECT COUNT(*) n FROM items WHERE status='ready'")["n"]
    out_rows = vault.rows("SELECT kind, COUNT(*) n FROM outputs GROUP BY kind")
    by_kind = {r["kind"]: r["n"] for r in out_rows}
    attempts = vault.rows("SELECT * FROM quiz_attempts ORDER BY id")
    reviews = vault.one("SELECT COUNT(*) n FROM events WHERE kind='review'")["n"]
    events = vault.rows("SELECT created_at FROM events")
    chats = vault.one("SELECT COUNT(*) n FROM events WHERE kind='chat'")["n"]
    cards_total = vault.one("SELECT COUNT(*) n FROM cards")["n"]
    due = vault.one("SELECT COUNT(*) n FROM cards WHERE due<=?", (date.today().isoformat(),))["n"]

    day_counts = Counter(_day(e["created_at"]) for e in events)
    today = date.today()
    daily = [{"label": (today - timedelta(days=i)).strftime("%d %b"), "value": day_counts.get(today - timedelta(days=i), 0)}
             for i in range(13, -1, -1)]
    monday = today - timedelta(days=today.weekday())
    weekly = []
    for w in range(7, -1, -1):
        start = monday - timedelta(weeks=w)
        total = sum(day_counts.get(start + timedelta(days=d), 0) for d in range(7))
        weekly.append({"label": start.strftime("%d %b"), "value": total})

    quiz_perf = [{"label": f"#{a['id']}", "value": round(100 * a["correct"] / a["total"]) if a["total"] else 0}
                 for a in attempts[-15:]]
    weak = Counter()
    for a in attempts:
        for t in json.loads(a["weak"] or "[]"):
            weak[t] += 1
    weak_areas = [{"topic": t, "misses": n} for t, n in weak.most_common(8)]
    best = max((round(100 * a["correct"] / a["total"]) for a in attempts if a["total"]), default=0)
    avg = round(sum(p["value"] for p in quiz_perf) / len(quiz_perf)) if quiz_perf else 0
    streak = _streak(set(day_counts))
    exam_made = by_kind.get("exam", 0)

    def ach(id_, title, desc, value, target):
        return {"id": id_, "title": title, "desc": desc, "earned": value >= target,
                "value": min(value, target), "target": target}

    achievements = [
        ach("first", "First lecture", "Upload your first material", items, 1),
        ach("notes10", "Note taker", "Generate 10 sets of notes", by_kind.get("notes", 0), 10),
        ach("quiz5", "Quiz regular", "Finish 5 quizzes", len(attempts), 5),
        ach("perfect", "Perfect score", "Score 100% on a quiz", best, 100),
        ach("cards50", "Memory builder", "Review 50 flashcards", reviews, 50),
        ach("streak7", "Seven-day streak", "Study 7 days in a row", streak, 7),
        ach("exam", "Exam ready", "Create an Exam Mode bundle", exam_made, 1),
    ]
    weekly_now = weekly[-1]["value"]
    report = (f"You have {items} study item{'s' if items != 1 else ''} and {cards_total} flashcards, with {due} due today. "
              f"This week you logged {weekly_now} study action{'s' if weekly_now != 1 else ''}. ")
    if quiz_perf:
        report += f"Your quiz average is {avg}% (best {best}%). "
        if weak_areas:
            report += f"Revise next: {', '.join(w['topic'] for w in weak_areas[:3])}."
    else:
        report += "Take a quiz to see your weak areas here."
    return {"items": items, "by_kind": by_kind, "quizzes": len(attempts), "reviews": reviews, "chats": chats,
            "cards_total": cards_total, "due": due, "streak": streak, "daily": daily, "weekly": weekly,
            "quiz_perf": quiz_perf, "quiz_avg": avg, "weak_areas": weak_areas, "achievements": achievements,
            "report": report}
