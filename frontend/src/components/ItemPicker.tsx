import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { api } from "../api/client";
import type { InventoryItem } from "../api/types";
import { chooseTypedItem } from "../utils/itemPick";

type Props = {
  value: string;
  onSelect: (item: InventoryItem) => void;
  /** Focus (and open) as soon as it mounts — for pickers that replace a
   *  "Change" button, so the user can type straight away. */
  autoFocus?: boolean;
  /** Called on Escape, or when focus leaves without a pick. */
  onCancel?: () => void;
  disabled?: boolean;
  placeholder?: string;
};

type Pos = { top: number; left: number; width: number };

/** Move focus to the next focusable field after `from`, like Tab would. */
function focusNext(from: HTMLElement | null) {
  if (!from) return;
  const all = Array.from(
    document.querySelectorAll<HTMLElement>("input, select, textarea, button, [tabindex]"),
  ).filter(
    (el) => !(el as HTMLInputElement).disabled && el.tabIndex >= 0 && el.offsetParent !== null,
  );
  const next = all[all.indexOf(from) + 1];
  if (!next) return;
  next.focus();
  if (next instanceof HTMLInputElement) next.select();
}

/**
 * Stock item search box.
 *
 * Commit rules (see chooseTypedItem — the picker never guesses):
 *  - Click a row: that item.
 *  - Enter / Tab: the highlighted row (arrow keys), else the item whose code
 *    is EXACTLY what was typed, else the only match. Otherwise nothing is
 *    picked and the list stays open with a hint.
 *  - Results that are still loading for what was typed are waited for — a
 *    commit never uses results for an earlier keystroke.
 *  - Leaving the box by mouse with an exact code typed picks that code
 *    (instead of silently going back to the old item); anything else
 *    reverts to the line's current item.
 */
export function ItemPicker({ value, onSelect, autoFocus, onCancel, disabled, placeholder }: Props) {
  const [query, setQuery] = useState<string | null>(null);
  const [results, setResults] = useState<InventoryItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);
  const [highlight, setHighlight] = useState(-1);
  const [hint, setHint] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  // Mirrors for async code paths (a commit that waits for a search).
  const queryRef = useRef<string | null>(null);
  const resultsFor = useRef<string | null>(null); // the query `results` belong to
  const resultsRef = useRef<InventoryItem[]>([]);
  const highlightRef = useRef(-1);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const isTyping = query !== null;
  const shownValue = isTyping ? (query as string) : value;
  const searchFor = isTyping ? (query as string) : "";

  function changeQuery(q: string | null) {
    queryRef.current = q;
    setQuery(q);
    setHighlight(-1);
    highlightRef.current = -1;
    setHint(null);
  }

  function storeResults(forQuery: string, items: InventoryItem[]) {
    resultsFor.current = forQuery;
    resultsRef.current = items;
    setResults(items);
    setHighlight(-1);
    highlightRef.current = -1;
  }

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.searchInventory(searchFor);
        if (alive) storeResults(searchFor, data);
      } finally {
        if (alive) setLoading(false);
      }
    }, 80);
    return () => { alive = false; clearTimeout(t); };
  }, [searchFor, open]);

  useEffect(() => {
    if (autoFocus && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
    // Mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useLayoutEffect(() => {
    if (!open || !inputRef.current) return;
    const update = () => {
      const r = inputRef.current!.getBoundingClientRect();
      setPos({ top: r.bottom + 2, left: r.left, width: Math.max(r.width, 460) });
    };
    update();
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [open]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      const target = e.target as Node;
      if (
        inputRef.current && !inputRef.current.contains(target) &&
        dropdownRef.current && !dropdownRef.current.contains(target)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // Keep the highlighted row visible while arrowing through the list.
  useEffect(() => {
    if (highlight < 0) return;
    const row = dropdownRef.current?.querySelector<HTMLElement>(`[data-idx="${highlight}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  function commit(item: InventoryItem) {
    // flushSync: the line shows the new item before focus moves on.
    flushSync(() => {
      onSelectRef.current(item);
      changeQuery(null);
      setOpen(false);
    });
  }

  /** The results for exactly `q`: the ones on screen when they're for `q`,
   *  else a fresh search. Null when the user kept typing meanwhile (or left). */
  async function resultsForQuery(q: string): Promise<InventoryItem[] | null> {
    if (resultsFor.current === q) return resultsRef.current;
    setLoading(true);
    try {
      const items = await api.searchInventory(q);
      if (queryRef.current === q) storeResults(q, items);
      return items;
    } catch {
      if (queryRef.current === q) setHint("Couldn't search — try again.");
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function commitTyped(advance: boolean) {
    const q = queryRef.current;
    if (q === null || !q.trim()) return;
    const items = await resultsForQuery(q);
    if (items === null || queryRef.current !== q) return; // kept typing / left
    const pick = chooseTypedItem(q, items, highlightRef.current);
    if (!pick) {
      setOpen(true);
      setHint(
        items.length === 0
          ? `No item matches "${q.trim()}".`
          : `More than one item matches "${q.trim()}" — use ↑ ↓ and Enter, or click one.`,
      );
      return;
    }
    commit(pick);
    if (advance) focusNext(inputRef.current);
  }

  function moveHighlight(step: 1 | -1) {
    // Only within the list for exactly what's typed — never a stale one.
    if (resultsFor.current !== (queryRef.current ?? "")) return;
    const n = resultsRef.current.length;
    if (n === 0) return;
    const cur = highlightRef.current;
    const next = cur < 0 ? (step === 1 ? 0 : n - 1) : (cur + step + n) % n;
    highlightRef.current = next;
    setHighlight(next);
    setHint(null);
  }

  return (
    <>
      <input
        ref={inputRef}
        className="cell-input"
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder ?? "Item / description"}
        value={shownValue}
        disabled={disabled}
        onMouseDown={(e) => {
          if (disabled) return;
          const el = e.currentTarget;
          const alreadyFocused = document.activeElement === el;
          if (!alreadyFocused) {
            e.preventDefault();
            el.focus();
            el.select();
          }
          setOpen(true);
        }}
        onFocus={() => {
          setOpen(true);
        }}
        onBlur={() => {
          const q = queryRef.current;
          changeQuery(null);
          setOpen(false);
          onCancel?.();
          // Left by mouse with an EXACT code typed (e.g. typed 012, clicked
          // into Qty): take that code rather than silently keeping the old
          // item. Anything that isn't an exact code reverts.
          if (q && q.trim()) {
            const typed = q;
            const known = resultsFor.current === typed ? resultsRef.current : null;
            const apply = (items: InventoryItem[]) => {
              const exact = chooseTypedItem(typed, items, -1, true);
              if (exact) onSelectRef.current(exact);
            };
            if (known) apply(known);
            else api.searchInventory(typed).then(apply).catch(() => {});
          }
        }}
        onChange={(e) => {
          changeQuery(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          const typing = queryRef.current !== null && queryRef.current.trim() !== "";
          if ((e.key === "ArrowDown" || e.key === "ArrowUp") && open && resultsRef.current.length > 0) {
            // Stay inside the list: the line grid uses arrows to change row.
            e.preventDefault();
            e.stopPropagation();
            moveHighlight(e.key === "ArrowDown" ? 1 : -1);
          } else if (e.key === "Enter" && (typing || highlightRef.current >= 0)) {
            e.preventDefault();
            if (!typing && highlightRef.current >= 0) {
              const item =
                resultsFor.current === (queryRef.current ?? "") ? resultsRef.current[highlightRef.current] : undefined;
              if (item) commit(item);
              return;
            }
            void commitTyped(false);
          } else if (e.key === "Tab" && !e.shiftKey && typing) {
            const q = queryRef.current as string;
            if (resultsFor.current === q) {
              // Results are for exactly what was typed: decide now and let
              // the browser's Tab move focus as usual.
              const pick = chooseTypedItem(q, resultsRef.current, highlightRef.current);
              if (pick) {
                commit(pick);
                return;
              }
            }
            // Still loading, or no single answer: hold focus here, wait for
            // the results, then commit + move on (or show why not).
            e.preventDefault();
            void commitTyped(true);
          } else if (e.key === "Escape") {
            e.preventDefault();
            changeQuery(null);
            setOpen(false);
            onCancel?.();
          }
        }}
      />
      {open && pos && createPortal(
        <div
          ref={dropdownRef}
          className="picker-dropdown"
          style={{ position: "fixed", top: pos.top, left: pos.left, minWidth: pos.width }}
        >
          {hint && (
            <div className="picker-empty" style={{ color: "#b45309", fontWeight: 600 }}>{hint}</div>
          )}
          {loading && <div className="picker-empty">Loading…</div>}
          {!loading && results.length === 0 && !hint && <div className="picker-empty">No items</div>}
          {!loading &&
            results.map((it, idx) => (
              <div
                key={it.code}
                data-idx={idx}
                className={`picker-row${idx === highlight ? " picker-row--highlight" : ""}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => commit(it)}
              >
                <span className="picker-code">{it.code}</span>
                <span className="picker-name">{it.description}</span>
              </div>
            ))}
        </div>,
        document.body
      )}
    </>
  );
}
