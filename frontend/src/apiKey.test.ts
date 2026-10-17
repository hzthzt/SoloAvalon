import assert from "node:assert/strict";
import test from "node:test";

import { maskApiKeyForDisplay } from "./apiKey.js";

test("partially masks long api keys without revealing their length", () => {
  const apiKey = "xxxxxx-secret-value-xxxxx";
  const masked = maskApiKeyForDisplay(apiKey);

  assert.equal(masked, "xxxx********xxxx");
});

test("fully masks short api keys", () => {
  assert.equal(maskApiKeyForDisplay("short"), "*****");
  assert.equal(maskApiKeyForDisplay("123456789012345"), "***************");
  assert.equal(maskApiKeyForDisplay(""), "");
});
