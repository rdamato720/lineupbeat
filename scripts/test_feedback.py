#!/usr/bin/env python3
"""Static safety checks for the reader feedback surface and service."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    template = (ROOT / "site" / "template.html").read_text()
    seo = (ROOT / "scripts" / "seo.py").read_text()
    widget = (ROOT / "site" / "feedback.js").read_text()
    worker = (ROOT / "feedback-worker" / "src" / "index.js").read_text()
    migration = (ROOT / "feedback-worker" / "migrations" /
                 "0001_feedback.sql").read_text()

    assert '<script defer src="/feedback.js"></script>' in template
    assert '<script defer src="/feedback.js"></script>' in seo
    assert "https://feedback.lineupbeat.com/feedback" in widget
    assert "data.page_url=location.href" in widget
    assert "ADMIN_TOKEN" in worker and "IP_HASH_SALT" in worker
    assert "CF-Connecting-IP" in worker
    assert "ip_hash TEXT NOT NULL" in migration
    assert "CREATE INDEX IF NOT EXISTS feedback_rate_idx" in migration
    forbidden = ("REPLACE_WITH_D1_DATABASE_ID\"\n", "Bearer REPLACE", "sk-")
    for secret in forbidden:
        assert secret not in worker
    print("feedback widget and Worker safety checks passed")


if __name__ == "__main__":
    main()
