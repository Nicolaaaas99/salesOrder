import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export type SelectOption = {
  key: string | number;
  display: string;
  description?: string;
  extra?: string;
};

type Props = {
  value: string | number | null;
  options: SelectOption[];
  onChange: (key: string | number | null) => void;
  placeholder?: string;
  disabled?: boolean;
  readOnly?: boolean;
  allowClear?: boolean;
  minDropdownWidth?: number;
  className?: string;
};

type Pos = { top: number; left: number; width: number };

export function SelectCell({
  value, options, onChange,
  placeholder = "—", disabled = false, readOnly = false,
  allowClear = true, minDropdownWidth = 260, className = "cell-input",
}: Props) {
  const current = options.find((o) => String(o.key) === String(value));
  const displayValue = current?.display ?? "";
  const title = current
    ? [current.display, current.description, current.extra].filter(Boolean).join(" — ")
    : undefined;

  const [query, setQuery] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const isTyping = query !== null;
  const shownValue = isTyping ? (query as string) : displayValue;
  const typedTrimmed = isTyping ? (query as string).trim() : "";

  const trimmedLower = typedTrimmed.toLowerCase();
  const filtered = trimmedLower
    ? options.filter((o) =>
        o.display.toLowerCase().includes(trimmedLower) ||
        (o.description || "").toLowerCase().includes(trimmedLower)
      )
    : options;

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

  function commit(opt: SelectOption | null) {
    onChange(opt?.key ?? null);
    setQuery(null);
    setOpen(false);
  }

  return (
    <>
      <input
        ref={inputRef}
        className={className}
        type="text"
        autoComplete="off"
        spellCheck={false}
        value={shownValue}
        placeholder={placeholder}
        disabled={disabled}
        readOnly={readOnly}
        title={title}
        onMouseDown={(e) => {
          if (disabled) return;
          const el = e.currentTarget;
          if (document.activeElement === el) return;
          e.preventDefault();
          el.focus();
          if (!readOnly) el.select();
          setOpen(true);
        }}
        onFocus={(e) => {
          if (disabled) return;
          setOpen(true);
          if (readOnly) return;
          const el = e.currentTarget;
          requestAnimationFrame(() => el.select());
        }}
        onBlur={() => {
          setOpen(false);
          if (isTyping && typedTrimmed === "" && current) {
            onChange(null);
          }
          setQuery(null);
        }}
        onChange={(e) => {
          if (readOnly) return;
          setQuery(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (readOnly) return;
          if (e.key === "Tab" && open && filtered.length > 0) {
            commit(filtered[0]);
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
          {readOnly ? (
            current ? (
              <div className="picker-row picker-row--info">
                <span className="picker-code">{current.display}</span>
                {current.description && <span className="picker-name">{current.description}</span>}
                {current.extra && <span className="picker-extra">{current.extra}</span>}
              </div>
            ) : (
              <div className="picker-empty">Nothing selected</div>
            )
          ) : (
            <>
              {allowClear && (
                <div
                  className="picker-row picker-row--muted"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => commit(null)}
                >
                  <span className="picker-name muted">— (none)</span>
                </div>
              )}
              {filtered.length === 0 ? (
                <div className="picker-empty">No matches</div>
              ) : (
                filtered.map((o) => (
                  <div
                    key={o.key}
                    className="picker-row"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => commit(o)}
                  >
                    <span className="picker-code">{o.display}</span>
                    {o.description && <span className="picker-name">{o.description}</span>}
                    {o.extra && <span className="picker-extra">{o.extra}</span>}
                  </div>
                ))
              )}
            </>
          )}
        </div>,
        document.body
      )}
    </>
  );
}
