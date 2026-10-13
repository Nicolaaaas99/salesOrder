import { useEffect, useRef, useState } from "react";

type Props = {
  value: number;
  onChange: (v: number) => void;
  className?: string;
  placeholder?: string;
  disabled?: boolean;
  title?: string;
  min?: number;
  max?: number;
  step?: number;
  /** If set (> 1), the value is snapped to the nearest multiple of this on blur. */
  snapStep?: number;
  /** If true, clicking the input moves the caret to the end so backspacing works
   *  immediately. Doesn't affect keyboard focus (tab/arrow) which already lands
   *  at the end by default. */
  cursorToEnd?: boolean;
};

const MAX_DECIMALS = 2;
const inputPattern = new RegExp(`^\\d*\\.?\\d{0,${MAX_DECIMALS}}$`);

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function trimTo2Decimals(raw: string): string {
  const dot = raw.indexOf(".");
  if (dot === -1) return raw;
  return raw.slice(0, dot + 1 + MAX_DECIMALS);
}

function snapToStep(n: number, step: number): number {
  if (!step || step <= 1 || n <= 0) return n;
  const packs = Math.max(1, Math.round(n / step));
  return packs * step;
}

export function NumberCell({ value, onChange, className, placeholder = "0", disabled, title, snapStep, cursorToEnd }: Props) {
  // Round `value` before stringifying so float noise like 11.200999999999999
  // doesn't make it into the input text.
  const [text, setText] = useState<string>(() => {
    const r = round2(value);
    return r === 0 ? "" : String(r);
  });
  const lastValue = useRef(value);

  useEffect(() => {
    if (value !== lastValue.current) {
      lastValue.current = value;
      const r = round2(value);
      setText(r === 0 ? "" : String(r));
    }
  }, [value]);

  function commit(raw: string) {
    setText(raw);
    const n = raw === "" || raw === "." ? 0 : parseFloat(raw);
    const clean = isNaN(n) ? 0 : round2(n);
    lastValue.current = clean;
    onChange(clean);
  }

  function onBlur() {
    if (!snapStep || snapStep <= 1) return;
    const snapped = snapToStep(lastValue.current, snapStep);
    if (snapped !== lastValue.current) {
      lastValue.current = snapped;
      setText(snapped === 0 ? "" : String(snapped));
      onChange(snapped);
    }
  }

  // When the user clicks the input, the browser places the caret where they
  // clicked. If `cursorToEnd` is on, override that so backspace deletes the
  // last char immediately (useful for Qty-style fields with short values).
  function moveCaretToEnd(el: HTMLInputElement) {
    const end = el.value.length;
    el.setSelectionRange(end, end);
  }

  return (
    <input
      className={className}
      type="text"
      inputMode="decimal"
      value={text}
      placeholder={placeholder}
      disabled={disabled}
      title={title}
      onBlur={onBlur}
      onMouseUp={cursorToEnd ? (e) => moveCaretToEnd(e.currentTarget) : undefined}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw !== "" && !inputPattern.test(raw)) return;
        commit(raw);
      }}
      onPaste={(e) => {
        const pasted = e.clipboardData.getData("text");
        const cleaned = pasted.replace(/[^\d.]/g, "");
        const firstDot = cleaned.indexOf(".");
        const singleDot = firstDot === -1
          ? cleaned
          : cleaned.slice(0, firstDot + 1) + cleaned.slice(firstDot + 1).replace(/\./g, "");
        const normalized = trimTo2Decimals(singleDot);
        if (normalized) {
          e.preventDefault();
          commit(normalized);
        }
      }}
    />
  );
}
