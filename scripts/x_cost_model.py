"""Cost model for X pay-per-use at beat-writer scale."""
READ = 0.005  # $ per post fetched

def naive(writers, polls_per_day, posts_returned):
    """Poll a timeline, get the last N posts back every time, pay for all of them."""
    return writers * polls_per_day * posts_returned * READ

def incremental(writers, new_posts_per_day):
    """since_id: only new posts are fetched, so only new posts are billed."""
    return writers * new_posts_per_day * READ

print(f"{'scenario':<52} {'$/day':>9} {'$/month':>10}")
print("-" * 74)
rows = [
    ("naive: 200 writers, poll q20m 14h, 10 posts back", naive(200, 42, 10)),
    ("naive: 200 writers, poll hourly 14h, 10 posts back", naive(200, 14, 10)),
    ("since_id: 200 writers, 20 new posts/day each", incremental(200, 20)),
    ("since_id: 200 writers, 10 new posts/day each", incremental(200, 10)),
    ("since_id: 64 writers (2/team), 20 new posts/day", incremental(64, 20)),
    ("since_id: 32 writers (1/team), 20 new posts/day", incremental(32, 20)),
]
for label, daily in rows:
    print(f"{label:<52} {daily:>9.2f} {daily*30:>10.2f}")

print("\nSunday inactives window only (10:30am-1:00pm ET):")
for w in (32, 64, 200):
    d = incremental(w, 12) / 7   # one day in seven, ~12 posts each in-window
    print(f"  {w:>3} writers, ~12 posts each: ${d*30:.2f}/month")
