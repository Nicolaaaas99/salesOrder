import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import type { Project } from "../api/types";

type Props = {
  value: string;
  onSelect: (code: string) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  allowClear?: boolean;
  minDropdownWidth?: number;
};

type Pos = { top: number; left: number; width: number };

export function ProjectPicker({
  value, onSelect,
  placeholder = "Select a Project",
  className = "input",
  disabled = false,
  allowClear = true,
  minDropdownWidth = 300,
}: Props) {
  const [query, setQuery] = useState<string | null>(null);
  const [results, setResults] = useState<Project[]>([]);
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
        const data = await api.searchProjects(searchFor);
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
      setPos({ top: r.bottom + 2, left: r.left, width: Math.max(r.width, minDropdownWidth) });
    };
    update();
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [open, minDropdownWidth]);

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
        className={className}
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder}
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
          if (disabled) return;
          setOpen(true);
        }}
        onBlur={() => {
          setOpen(false);
          if (allowClear && isTyping && (query as string).trim() === "" && value) {
            onSelect("");
          }
          setQuery(null);
        }}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Tab" && open && isTyping && (query as string).trim() && results.length > 0) {
            const top = results[0];
            onSelect(top.code);
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
          {!loading && allowClear && (
            <div
              className="picker-row picker-row--muted"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                onSelect("");
                setQuery(null);
                setOpen(false);
              }}
            >
              <span className="picker-name muted">— (none)</span>
            </div>
          )}
          {!loading && results.length === 0 && <div className="picker-empty">No projects</div>}
          {!loading &&
            results.map((p) => (
              <div
                key={p.code}
                className="picker-row"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onSelect(p.code);
                  setQuery(null);
                  setOpen(false);
                }}
              >
                <span className="picker-code">{p.code}</span>
                <span className="picker-name">{p.description}</span>
              </div>
            ))}
        </div>,
        document.body
      )}
    </>
  );
}
