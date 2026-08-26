import test from "node:test";
import assert from "node:assert/strict";
import { validateSubmission } from "../src/index.js";

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

