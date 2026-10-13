import { useEffect, useRef, useState } from "react";

type Props = {
  value: string;
  onChange: (iso: string) => void;
  className?: string;
};

export function DateInput({ value, onChange, className = "input" }: Props) {
  const [text, setText] = useState(isoToDmy(value));
  const pickerRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setText(isoToDmy(value));
  }, [value]);

  function commitText(next: string) {
    const digits = next.replace(/\D/g, "").slice(0, 8);
    const formatted = formatDmy(digits);
    setText(formatted);
    const iso = dmyToIso(formatted);
    if (iso) onChange(iso);
    else if (digits.length === 0) onChange("");
  }

  return (
    <div className="date-wrap">
      <input
        className={className}
        type="text"
        inputMode="numeric"
        placeholder="dd/mm/yyyy"
        value={text}
        onChange={(e) => commitText(e.target.value)}
        onBlur={() => {
          const iso = parseAnyToIso(text);
          if (iso) {
            setText(isoToDmy(iso));
            onChange(iso);
          } else if (text.replace(/\D/g, "").length === 0) {
            setText("");
            onChange("");
          } else {
            setText(isoToDmy(value));
          }
        }}
        onPaste={(e) => {
          const pasted = e.clipboardData.getData("text");
          const iso = parseAnyToIso(pasted);
          if (iso) {
            e.preventDefault();
            setText(isoToDmy(iso));
            onChange(iso);
          }
        }}
      />
      <button
        type="button"
        className="date-picker-btn"
        title="Pick a date"
        onClick={() => {
          const inp = pickerRef.current;
          if (!inp) return;
          try {
            (inp as HTMLInputElement & { showPicker: () => void }).showPicker();
          } catch {
            inp.focus();
            inp.click();
          }
        }}
      >
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        >
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
      </button>
      <input
        ref={pickerRef}
        type="date"
        className="date-picker-hidden"
        value={value}
        tabIndex={-1}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function formatDmy(digits: string): string {
  const d = digits.slice(0, 8);
  if (d.length <= 2) return d;
  if (d.length <= 4) return `${d.slice(0, 2)}/${d.slice(2)}`;
  return `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}`;
}

function dmyToIso(dmy: string): string | null {
  const digits = dmy.replace(/\D/g, "");
  if (digits.length !== 8) return null;
  const dd = digits.slice(0, 2);
  const mm = digits.slice(2, 4);
  const yyyy = digits.slice(4, 8);
  return validate(yyyy, mm, dd);
}

function isoToDmy(iso: string): string {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return "";
  return `${m[3]}/${m[2]}/${m[1]}`;
}

function parseAnyToIso(raw: string): string | null {
  const s = raw.trim();
  if (!s) return null;

  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;

  let m = s.match(/^(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})$/);
  if (m) return validate(m[1], m[2], m[3]);

  m = s.match(/^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})$/);
  if (m) return validate(m[3], m[2], m[1]);

  const digits = s.replace(/\D/g, "");
  if (digits.length === 8) {
    return validate(digits.slice(4, 8), digits.slice(2, 4), digits.slice(0, 2));
  }
  return null;
}

function validate(y: string, mo: string, d: string): string | null {
  const mn = Number(mo), dn = Number(d);
  if (mn < 1 || mn > 12 || dn < 1 || dn > 31) return null;
  return `${y}-${String(mn).padStart(2, "0")}-${String(dn).padStart(2, "0")}`;
}
