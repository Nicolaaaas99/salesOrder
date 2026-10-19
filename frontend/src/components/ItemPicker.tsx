import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import type { InventoryItem } from "../api/types";

type Props = {
  value: string;
  onSelect: (item: InventoryItem) => void;
};

type Pos = { top: number; left: number; width: number };

export function ItemPicker({ value, onSelect }: Props) {
  const [query, setQuery] = useState<string | null>(null);
  const [results, setResults] = useState<InventoryItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const isTyping = query !== null;
  const shownValue = isTyping ? (query as string) : value;
  const searchFor = isTyping ? (query as string) : "";

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.searchInventory(searchFor);
        if (alive) setResults(data);
      } finally {
        if (alive) setLoading(false);
      }
    }, 80);
    return () => { alive = false; clearTimeout(t); };
  }, [searchFor, open]);

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

  return (
    <>
      <input
        ref={inputRef}
        className="cell-input"
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder="Item / description"
        value={shownValue}
        onMouseDown={(e) => {
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
          setOpen(false);
          setQuery(null);
        }}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          // Enter commits ONLY on an exact code match — so typing "131" and
          // pressing Enter won't accidentally add "905131". Tab still commits
          // the top fuzzy match (unchanged) so the quick-type workflow for
          // auto-complete still works.
          if (e.key === "Enter" && open && isTyping) {
            e.preventDefault();
            const q = (query as string).trim();
            if (!q || results.length === 0) return;
            const exact = results.find(
              (r) => r.code.toLowerCase() === q.toLowerCase(),
            );
            if (!exact) return; // no exact match → do nothing
            onSelect(exact);
            setQuery(null);
            setOpen(false);
          } else if (
            e.key === "Tab" &&
            open &&
            isTyping &&
            (query as string).trim() &&
            results.length > 0
          ) {
            const top = results[0];
            onSelect(top);
            setQuery(null);
            setOpen(false);
          } else if (e.key === "Escape") {
            e.preventDefault();
            setQuery(null);
            setOpen(false);
          }
        }}
      />
      {open && pos && createPortal(
        <div
          ref={dropdownRef}
          className="picker-dropdown"
          style={{ position: "fixed", top: pos.top, left: pos.left, minWidth: pos.width }}
        >
          {loading && <div className="picker-empty">Loading…</div>}
          {!loading && results.length === 0 && <div className="picker-empty">No items</div>}
          {!loading &&
            results.map((it) => (
              <div
                key={it.code}
                className="picker-row"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onSelect(it);
                  setQuery(null);
                  setOpen(false);
                }}
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
