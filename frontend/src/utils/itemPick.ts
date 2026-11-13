import type { InventoryItem } from "../api/types";

/**
 * Which item a keyboard commit (Tab / Enter / leaving the box) takes from
 * the item picker. NEVER a guess (2026-11-13: Tab used to take the TOP
 * search result, and the results could still be for an earlier keystroke —
 * typing "012" + Tab quickly added "001 Milk Fresh 4 L"; even fresh results
 * listed "0012" or a description containing "012" before "012" itself).
 *
 * `items` MUST be the results for exactly `typed` (the caller checks).
 *  1. a row the user highlighted with the arrow keys
 *  2. the item whose CODE is exactly what was typed (case/space-insensitive)
 *  3. the only result, when just one item matches
 * Anything else returns null: the picker stays open for the user to choose.
 * `exactOnly` (focus left the box by mouse): only rule 2 applies.
 */
export function chooseTypedItem(
  typed: string,
  items: InventoryItem[],
  highlighted: number,
  exactOnly = false,
): InventoryItem | null {
  const t = typed.trim().toLowerCase();
  if (!t) return null;
  if (!exactOnly && highlighted >= 0 && highlighted < items.length) return items[highlighted];
  const exact = items.find((r) => r.code.trim().toLowerCase() === t);
  if (exact) return exact;
  if (!exactOnly && items.length === 1) return items[0];
  return null;
}
