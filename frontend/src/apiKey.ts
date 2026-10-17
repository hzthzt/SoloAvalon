const VISIBLE_PREFIX_LENGTH = 4;
const VISIBLE_SUFFIX_LENGTH = 4;
const MASK = "********";
const MIN_PARTIAL_MASK_LENGTH = 16;

export function maskApiKeyForDisplay(apiKey: string): string {
  if (!apiKey) {
    return "";
  }
  if (apiKey.length < MIN_PARTIAL_MASK_LENGTH) {
    return "*".repeat(apiKey.length);
  }
  return `${apiKey.slice(0, VISIBLE_PREFIX_LENGTH)}${MASK}${apiKey.slice(-VISIBLE_SUFFIX_LENGTH)}`;
}
