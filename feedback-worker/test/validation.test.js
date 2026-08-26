import test from "node:test";
import assert from "node:assert/strict";
import { feedbackEmail, validateSubmission } from "../src/index.js";

test("accepts useful Lineup Beat feedback", () => {
  const result = validateSubmission({
    category: "feature", message: "Please add dynasty comparison filters.",
    email: "reader@example.com", page_url: "https://lineupbeat.com/nfl/compare/",
  });
  assert.deepEqual(result.errors, []);
  assert.equal(result.value.category, "FEATURE");
});

test("rejects spam, short messages and foreign pages", () => {
  const result = validateSubmission({
    category: "general", message: "Hi", website: "spam",
    page_url: "https://example.com/",
  });
  assert.ok(result.errors.length >= 3);
});

test("builds a restricted plain-text feedback notification", () => {
  const email = feedbackEmail({
    category: "FEATURE",
    message: "Please add dynasty comparison filters.",
    email: "reader@example.com",
    pageUrl: "https://lineupbeat.com/nfl/compare/",
  }, "feedback-id", "2026-08-26T17:00:00.000Z");
  assert.equal(email.to, "hello@lineupbeat.com");
  assert.equal(email.from.email, "feedback@lineupbeat.com");
  assert.match(email.subject, /feature feedback/i);
  assert.match(email.text, /reader@example.com/);
  assert.match(email.text, /feedback-id/);
  assert.equal("html" in email, false);
});
