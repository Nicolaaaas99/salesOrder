import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import type { CustomerSummary } from "../api/types";

type Props = {
  value: string;
  onSelect: (account: string) => void;
  onChange?: (text: string) => void;
  placeholder?: string;
  onEnter?: () => void;
};

type Pos = { top: number; left: number; width: number };

export function CustomerPicker({
  value, onSelect,
  onChange, placeholder = "Select a Customer", onEnter,
}: Props) {
  const controlled = onChange !== undefined;
  const [query, setQuery] = useState<string | null>(null);
  const [results, setResults] = useState<CustomerSummary[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);
  const [highlightIdx, setHighlightIdx] = useState(-1);

  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const isTyping = query !== null;
  const shownValue = controlled ? value : (isTyping ? (query as string) : value);
  const searchFor = controlled ? value : (isTyping ? (query as string) : "");

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.searchCustomers(searchFor);
        if (alive) {
          setResults(data);
          setHighlightIdx(data.length > 0 ? 0 : -1);
        }
      } finally {
        if (alive) setLoading(false);
      }
    }, 80);
    return () => { alive = false; clearTimeout(t); };
  }, [searchFor, open]);

  useEffect(() => {
    if (highlightIdx < 0 || !dropdownRef.current) return;
    const row = dropdownRef.current.querySelectorAll(".picker-row")[highlightIdx] as HTMLElement | undefined;
    row?.scrollIntoView({ block: "nearest" });
  }, [highlightIdx]);

  function commitSelection(c: CustomerSummary) {
    onSelect(c.account);
    setQuery(null);
    setOpen(false);
    setHighlightIdx(-1);
  }

  useLayoutEffect(() => {
    if (!open || !inputRef.current) return;
    const update = () => {
      const r = inputRef.current!.getBoundingClientRect();
      setPos({ top: r.bottom + 2, left: r.left, width: Math.max(r.width, 380) });
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
        className="input"
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder}
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
          if (!controlled) setQuery(null);
        }}
        onChange={(e) => {
          if (controlled) onChange!(e.target.value);
          else setQuery(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            if (results.length === 0) return;
            setOpen(true);
            setHighlightIdx((i) => (i + 1) % results.length);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            if (results.length === 0) return;
            setOpen(true);
            setHighlightIdx((i) => (i <= 0 ? results.length - 1 : i - 1));
          } else if (e.key === "Enter") {
            if (open && highlightIdx >= 0 && results[highlightIdx]) {
              e.preventDefault();
              commitSelection(results[highlightIdx]);
            } else if (onEnter) {
              e.preventDefault();
              setOpen(false);
              onEnter();
            }
          } else if (e.key === "Escape") {
            e.preventDefault();
            if (!controlled) setQuery(null);
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
          {!loading && results.length === 0 && <div className="picker-empty">No customers</div>}
          {!loading &&
            results.map((c, idx) => (
              <div
                key={c.account}
                className={
                  "picker-row" + (idx === highlightIdx ? " picker-row--highlight" : "")
                }
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setHighlightIdx(idx)}
                onClick={() => commitSelection(c)}
              >
                <span className="picker-code">{c.account}</span>
                <span className="picker-name">{c.name}</span>
              </div>
            ))}
        </div>,
        document.body
      )}
    </>
  );
}
