import { useEffect, useRef } from "react";
import type { Warehouse, TaxType } from "../api/types";
import { api } from "../api/client";
import { ItemPicker } from "./ItemPicker";
import { NumberCell } from "./NumberCell";
import { ProjectPicker } from "./ProjectPicker";
import { SelectCell } from "./SelectCell";
import { fmtNumber } from "../utils/format";

export type TaxMode = "Exclusive" | "Inclusive";

export type LineRow = {
  stock_code: string;
  item_description: string;
  description: string;
  warehouse_code: string;
  warehouse_codes: string[];
  available: number;
  quantity: number;
  uom: string;
  is_uom_item: boolean;
  is_whse_item: boolean;
  unit_price: number;         // INVARIANT: always the EXCL unit price
  unit_price_incl: number;    // Actual INCL from view (0 if unknown — fallback to computed)
  price_source: string;   // 'Customer Deal' | 'Group Deal' | 'Price List' | 'None' | ''
  price_locked: boolean;  // true when a price was resolved — rep can't override
  tax_type_id: number | null;
  discount_percent: number;
  project_code: string;
  pack_size: number;      // qty must be a multiple of this (1 = no constraint)
};

export function makeEmptyLine(defaultTaxTypeId: number | null = null): LineRow {
  return {
    stock_code: "",
    item_description: "",
    description: "",
    warehouse_code: "",
    warehouse_codes: [],
    available: 0,
    quantity: 1,
    uom: "",
    is_uom_item: false,
    is_whse_item: false,
    unit_price: 0,
    unit_price_incl: 0,
    price_source: "",
    price_locked: false,
    tax_type_id: defaultTaxTypeId,
    discount_percent: 0,
    project_code: "",
    pack_size: 1,
  };
}

/**
 * Snap a quantity to the nearest multiple of pack size (round half-up).
 * Returns at least one pack when qty > 0; qty 0 stays 0 so empty rows don't
 * jump to 1 just because the user tabbed through.
 */
export function snapToPack(qty: number, packSize: number): number {
  if (!packSize || packSize <= 1) return qty;
  if (qty <= 0) return 0;
  const crates = Math.max(1, Math.round(qty / packSize));
  return crates * packSize;
}

/**
 * Head-office/pollination warehouse — the one telesales orders ship from most
 * of the time. Default on any new line unless the stock isn't set up there.
 */
export const DEFAULT_WAREHOUSE_CODE = "01.HOF_POL";

/**
 * Pick the warehouse to pre-select on a new line. Prefers `DEFAULT_WAREHOUSE_CODE`
 * when the stock is set up there; otherwise falls back to the first available.
 */
export function pickDefaultWarehouse(available: string[]): string {
  if (available.includes(DEFAULT_WAREHOUSE_CODE)) return DEFAULT_WAREHOUSE_CODE;
  return available[0] ?? "";
}

type LinesUpdater = LineRow[] | ((prev: LineRow[]) => LineRow[]);

type Props = {
  lines: LineRow[];
  warehouses: Warehouse[];
  taxTypes: TaxType[];
  defaultTaxTypeId: number | null;
  taxMode: TaxMode;
  customerAccount: string | null;   // needed to resolve prices when items are picked
  onChange: (lines: LinesUpdater) => void;
};

function rateFor(line: LineRow, taxTypes: TaxType[]): number {
  return taxTypes.find((t) => t.id === line.tax_type_id)?.rate ?? 0;
}

/**
 * Invariant: `line.unit_price` is ALWAYS the exclusive price. The Tax Mode
 * toggle is now display-only — it never changes stored values, so order totals
 * are stable regardless of which mode is active. See displayPrice() /
 * displayPriceToStored() for the UI-side conversion.
 *
 * `mode` is accepted for backwards compatibility with callers but ignored.
 */
export function lineAmounts(line: LineRow, taxTypes: TaxType[], _mode?: TaxMode) {
  const rate = rateFor(line, taxTypes);
  const discFactor = 1 - (line.discount_percent || 0) / 100;
  const excl = line.quantity * line.unit_price * discFactor;
  const tax = excl * (rate / 100);
  return { excl, tax, incl: excl + tax, rate };
}

export function orderTotals(
  lines: LineRow[],
  taxTypes: TaxType[],
  _mode: TaxMode,
  documentDiscount: number,
) {
  const raw = lines.reduce(
    (acc, l) => {
      const a = lineAmounts(l, taxTypes);
      return { excl: acc.excl + a.excl, tax: acc.tax + a.tax };
    },
    { excl: 0, tax: 0 },
  );
  const discFactor = 1 - (documentDiscount || 0) / 100;
  const excl = raw.excl * discFactor;
  const tax = raw.tax * discFactor;
  return { excl, tax, incl: excl + tax };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/**
 * What to show in the Price input/column for the current display mode.
 * Prefers the exact stored incl (from Price List / Evolution) in Inclusive
 * mode; falls back to computing from excl when it's missing (e.g. draft lines
 * added before a price was resolved).
 */
export function displayPrice(line: LineRow, taxTypes: TaxType[], mode: TaxMode): number {
  if (mode === "Inclusive") {
    if (line.unit_price_incl > 0) return line.unit_price_incl;
    const rate = rateFor(line, taxTypes);
    return rate > 0 ? round2(line.unit_price * (1 + rate / 100)) : line.unit_price;
  }
  return line.unit_price;
}

/**
 * Translate a value typed in the Price input back into both stored prices,
 * so the pair stays consistent regardless of which mode the user edits in.
 * Returns a partial LineRow patch.
 */
export function priceEditFromDisplay(
  typed: number,
  line: LineRow,
  taxTypes: TaxType[],
  mode: TaxMode,
): Partial<LineRow> {
  const rate = rateFor(line, taxTypes);
  if (mode === "Inclusive") {
    const incl = typed;
    const excl = rate > 0 ? round2(incl / (1 + rate / 100)) : incl;
    return { unit_price: excl, unit_price_incl: incl };
  }
  const excl = typed;
  const incl = rate > 0 ? round2(excl * (1 + rate / 100)) : excl;
  return { unit_price: excl, unit_price_incl: incl };
}

type FocusTarget = { row: number; col?: number };

export function LineItemsGrid({
  lines, warehouses, taxTypes, defaultTaxTypeId, taxMode, customerAccount, onChange,
}: Props) {
  const tbodyRef = useRef<HTMLTableSectionElement>(null);
  const focusTarget = useRef<FocusTarget | null>(null);
  const prevLineCount = useRef(lines.length);
  const editedInputs = useRef<WeakSet<HTMLElement>>(new WeakSet());

  function focusRowCol(rowIdx: number, colIdx?: number) {
    const rows = tbodyRef.current?.children;
    const row = rows?.[rowIdx] as HTMLTableRowElement | undefined;
    if (!row) return;
    let focusable: HTMLElement | null | undefined;
    if (colIdx !== undefined) {
      const cell = row.children[colIdx] as HTMLTableCellElement | undefined;
      focusable = cell?.querySelector<HTMLElement>(
        "input:not([disabled]), button:not([disabled]):not(.row-remove)"
      );
    }
    if (!focusable) {
      focusable = row.querySelector<HTMLElement>(
        "input:not([disabled]), button:not([disabled]):not(.row-remove)"
      );
    }
    focusable?.focus();
    if (focusable instanceof HTMLInputElement) focusable.select?.();
  }

  function focusInCells(cells: Element[], start: number, dir: -1 | 1): boolean {
    for (let i = start; i >= 0 && i < cells.length; i += dir) {
      const target = cells[i].querySelector<HTMLElement>(
        "input:not([disabled]), button:not([disabled]):not(.row-remove)"
      );
      if (target) {
        target.focus();
        if (target instanceof HTMLInputElement) target.select?.();
        return true;
      }
    }
    return false;
  }

  function navigateSideways(row: HTMLTableRowElement, currentCell: Element, dir: -1 | 1) {
    const cells = Array.from(row.children);
    const currentIdx = cells.indexOf(currentCell);
    if (focusInCells(cells, currentIdx + dir, dir)) return;

    const tbody = tbodyRef.current;
    if (!tbody) return;
    const rows = Array.from(tbody.children);
    const rowIdx = rows.indexOf(row);
    const neighborRow = rows[rowIdx + dir] as HTMLTableRowElement | undefined;
    if (!neighborRow) return;
    const neighborCells = Array.from(neighborRow.children);
    const start = dir === 1 ? 0 : neighborCells.length - 1;
    focusInCells(neighborCells, start, dir);
  }

  useEffect(() => {
    const target = focusTarget.current;
    if (target !== null && lines.length !== prevLineCount.current) {
      focusTarget.current = null;
      requestAnimationFrame(() => {
        const rows = tbodyRef.current?.children;
        if (!rows || rows.length === 0) return;
        const idx = Math.max(0, Math.min(target.row, rows.length - 1));
        focusRowCol(idx, target.col);
      });
    }
    prevLineCount.current = lines.length;
  }, [lines.length]);

  function updateLine(idx: number, patch: Partial<LineRow>) {
    onChange(lines.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  }
  function removeLineAt(idx: number, targetCol?: number) {
    focusTarget.current = { row: Math.max(0, idx - 1), col: targetCol };
    onChange(lines.filter((_, i) => i !== idx));
  }
  function appendLine(targetCol?: number) {
    focusTarget.current = { row: lines.length, col: targetCol };
    onChange([...lines, makeEmptyLine(defaultTaxTypeId)]);
  }

  function onGridKeyDown(e: React.KeyboardEvent<HTMLTableSectionElement>) {
    const target = e.target as HTMLElement;
    const cell = target.closest("td");
    const row = target.closest("tr") as HTMLTableRowElement | null;
    const tbody = tbodyRef.current;
    if (!cell || !row || !tbody) return;

    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      if (!(target instanceof HTMLInputElement)) return;
      if (editedInputs.current.has(target)) return;
      e.preventDefault();
      navigateSideways(row, cell, e.key === "ArrowLeft" ? -1 : 1);
      return;
    }

    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;

    const rowIndex = Array.from(tbody.children).indexOf(row);
    const colIndex = Array.from(row.children).indexOf(cell);
    if (rowIndex < 0 || colIndex < 0) return;

    if (e.key === "ArrowDown") {
      if (rowIndex < lines.length - 1) {
        e.preventDefault();
        focusRowCol(rowIndex + 1, colIndex);
      } else if (lines[rowIndex]?.stock_code) {
        e.preventDefault();
        appendLine(colIndex);
      }
    } else {
      if (rowIndex === 0) return;
      e.preventDefault();
      const currentEmpty = !lines[rowIndex]?.stock_code;
      if (currentEmpty && lines.length > 1) {
        removeLineAt(rowIndex, colIndex);
      } else {
        focusRowCol(rowIndex - 1, colIndex);
      }
    }
  }

  function onGridFocus(e: React.FocusEvent<HTMLTableSectionElement>) {
    if (e.target instanceof HTMLInputElement) {
      editedInputs.current.delete(e.target);
    }
  }

  function onGridInput(e: React.FormEvent<HTMLTableSectionElement>) {
    if (e.target instanceof HTMLInputElement) {
      editedInputs.current.add(e.target);
    }
  }

  const warehouseOptions = warehouses.map((w) => ({
    key: w.code, display: w.code, description: w.description,
  }));
  const taxOptions = taxTypes.map((t) => ({
    key: t.id, display: t.code, description: t.description, extra: `${t.rate}%`,
  }));

  const priceLabel = taxMode === "Inclusive" ? "Price (incl)" : "Price (excl)";
  const lineTotalLabel = taxMode === "Inclusive" ? "Line Incl" : "Line Excl";
  const lastLineHasItem = lines.length > 0 && !!lines[lines.length - 1].stock_code;

  return (
    <section className="panel grid-panel">
      <header className="panel-header">Lines</header>
      <div className="grid-scroll">
        <table className="line-grid">
          <thead>
            <tr>
              <th style={{ width: 30 }}>#</th>
              <th style={{ width: 42 }}>Mod</th>
              <th style={{ width: 130 }}>Item</th>
              <th style={{ width: 190 }}>Item Description</th>
              <th style={{ width: 180 }}>Description</th>
              <th style={{ width: 90 }}>Warehouse</th>
              <th style={{ width: 70 }}>Avail.</th>
              <th style={{ width: 50 }}>Pack</th>
              <th style={{ width: 70 }}>Qty</th>
              <th style={{ width: 50 }}>UOM</th>
              <th style={{ width: 100 }}>{priceLabel}</th>
              <th style={{ width: 80 }}>Tax</th>
              <th style={{ width: 55 }}>Rate %</th>
              <th style={{ width: 60 }}>Disc %</th>
              <th style={{ width: 100 }}>{lineTotalLabel}</th>
              <th style={{ width: 90 }}>Tax Amt</th>
              <th style={{ width: 100 }}>Project</th>
              <th style={{ width: 28 }}></th>
            </tr>
          </thead>
          <tbody
            ref={tbodyRef}
            onKeyDown={onGridKeyDown}
            onFocus={onGridFocus}
            onInput={onGridInput}
          >
            {lines.map((line, i) => {
              const { excl: lineExcl, tax: lineTax, incl: lineIncl, rate } = lineAmounts(line, taxTypes);
              const lineDisplayed = taxMode === "Inclusive" ? lineIncl : lineExcl;
              const hasItem = !!line.stock_code;
              const cellsDisabled = !hasItem;
              return (
                <tr key={i}>
                  <td className="num">{i + 1}</td>
                  <td>ST</td>
                  <td>
                    <ItemPicker
                      value={line.stock_code}
                      onSelect={(item) => {
                        const linked = item.warehouse_codes ?? [];
                        // If the user already picked a warehouse on this row
                        // and it's still valid for the new item, keep it.
                        // Otherwise default to 01.HOF_POL (telesales default).
                        const keptWhse = item.is_whse_item
                          ? line.warehouse_code &&
                            (linked.length === 0 ||
                              linked.includes(line.warehouse_code))
                            ? line.warehouse_code
                            : pickDefaultWarehouse(linked)
                          : "";
                        const pack = item.pack_size || 1;
                        updateLine(i, {
                          stock_code: item.code,
                          item_description: item.description,
                          description: item.description,
                          uom: item.uom,
                          is_uom_item: item.is_uom_item,
                          is_whse_item: item.is_whse_item,
                          available: item.available,
                          tax_type_id: item.default_tax_type_id ?? defaultTaxTypeId,
                          warehouse_code: keptWhse,
                          warehouse_codes: linked,
                          pack_size: pack,
                          // Snap current qty up to one pack so the row is valid immediately
                          quantity: snapToPack(line.quantity || pack, pack),
                          // Reset price state — will be filled by the resolver below
                          unit_price: 0,
                          price_source: "",
                          price_locked: false,
                        });

                        if (customerAccount) {
                          const pickedCode = item.code;
                          api
                            .getCustomerPrices(customerAccount, [pickedCode])
                            .then((map) => {
                              const p = map[pickedCode];
                              // Store both prices — unit_price = excl is the
                              // invariant used by totals/tax math, and
                              // unit_price_incl is the actual Price List incl
                              // used by the Incl display.
                              const excl = p?.excl_price ?? 0;
                              const incl = p?.incl_price ?? 0;
                              onChange((prev) =>
                                prev.map((l, idx) =>
                                  idx === i && l.stock_code === pickedCode
                                    ? {
                                        ...l,
                                        unit_price: excl,
                                        unit_price_incl: incl,
                                        price_source: p?.source ?? "None",
                                        price_locked: excl > 0,
                                      }
                                    : l,
                                ),
                              );
                            })
                            .catch(() => {
                              onChange((prev) =>
                                prev.map((l, idx) =>
                                  idx === i && l.stock_code === pickedCode
                                    ? { ...l, price_source: "None", price_locked: false }
                                    : l,
                                ),
                              );
                            });
                        }
                      }}
                    />
                  </td>
                  <td className="ellipsis" title={line.item_description}>{line.item_description}</td>
                  <td>
                    <input
                      className="cell-input"
                      value={line.description}
                      disabled={cellsDisabled}
                      maxLength={100}
                      onChange={(e) => updateLine(i, { description: e.target.value })}
                    />
                  </td>
                  <td>
                    <SelectCell
                      value={line.warehouse_code || null}
                      options={warehouseOptions.filter((w) =>
                        line.warehouse_codes.includes(w.key as string)
                      )}
                      disabled={cellsDisabled || !line.is_whse_item}
                      onChange={(v) => updateLine(i, { warehouse_code: (v as string) ?? "" })}
                    />
                  </td>
                  <td className="num">{line.is_whse_item ? fmtNumber(line.available) : ""}</td>
                  <td className="num">
                    {hasItem && line.pack_size > 1 ? `× ${line.pack_size}` : ""}
                  </td>
                  <td>
                    <NumberCell
                      className="cell-input num"
                      value={line.quantity}
                      disabled={cellsDisabled}
                      snapStep={line.pack_size > 1 ? line.pack_size : undefined}
                      onChange={(v) => updateLine(i, { quantity: v })}
                    />
                  </td>
                  <td>{line.is_uom_item ? line.uom : ""}</td>
                  <td>
                    <NumberCell
                      className="cell-input num"
                      value={displayPrice(line, taxTypes, taxMode)}
                      disabled={cellsDisabled || line.price_locked}
                      title={line.price_locked ? `Price from ${line.price_source}` : undefined}
                      onChange={(v) =>
                        updateLine(i, priceEditFromDisplay(v, line, taxTypes, taxMode))
                      }
                    />
                  </td>
                  <td>
                    <SelectCell
                      value={line.tax_type_id}
                      options={taxOptions}
                      minDropdownWidth={320}
                      disabled={cellsDisabled}
                      readOnly
                      allowClear={false}
                      onChange={(v) => updateLine(i, { tax_type_id: v == null ? null : Number(v) })}
                    />
                  </td>
                  <td className="num">{hasItem ? fmtNumber(rate) : ""}</td>
                  <td>
                    <NumberCell
                      className="cell-input num"
                      value={line.discount_percent}
                      disabled={cellsDisabled}
                      onChange={(v) => updateLine(i, { discount_percent: v })}
                    />
                  </td>
                  <td className="num">{hasItem ? fmtNumber(lineDisplayed) : ""}</td>
                  <td className="num">{hasItem ? fmtNumber(lineTax) : ""}</td>
                  <td>
                    <ProjectPicker
                      value={line.project_code}
                      className="cell-input"
                      disabled={cellsDisabled}
                      onSelect={(code) => updateLine(i, { project_code: code })}
                    />
                  </td>
                  <td>
                    <button className="row-remove" onClick={() => removeLineAt(i)} title="Remove line">×</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="grid-footer">
        <button
          className="btn btn-secondary"
          onClick={() => appendLine()}
          disabled={!lastLineHasItem}
          title={lastLineHasItem ? "" : "Pick an item on the last line before adding a new one"}
        >
          + Add Line
        </button>
      </div>
    </section>
  );
}
