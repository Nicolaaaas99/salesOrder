import { useEffect, useRef } from "react";
import { ArrowRight, Trash2, Plus, ListOrdered, Lock, Unlock } from "lucide-react";
import type {
  CustomerDetail,
  InventoryItem,
  TaxType,
} from "../api/types";
import { fmtCurrency, fmtNumber } from "../utils/format";
import {
  type LineRow,
  type TaxMode,
  lineAmounts,
  orderTotals,
  priceEditFromDisplay,
} from "./LineItemsGrid";
import { CreditStatus } from "./CreditStatus";
import { ItemPicker } from "./ItemPicker";
import { NumberCell } from "./NumberCell";

export type DraftLine = LineRow;

type Props = {
  customer: CustomerDetail | null;
  lines: DraftLine[];
  onUpdateLine: (index: number, patch: Partial<DraftLine>) => void;
  onRemoveLine: (index: number) => void;
  onClearAll: () => void;
  onAddItem: (item: InventoryItem) => void;
  onContinue: () => void;
  taxTypes: TaxType[];
  /** Object identity bumps whenever a line is added/merged; the Qty cell at
   *  `idx` is then focused + scrolled into view. */
  focusTarget?: { idx: number } | null;
  /** When set, the credit area shows a green "N today" pill. Clicking it
   *  calls `onShowOrdersToday`. */
  ordersTodayCount?: number;
  onShowOrdersToday?: () => void;
};

const taxMode: TaxMode = "Exclusive";

export function DraftOrderPanel({
  customer,
  lines,
  onUpdateLine,
  onRemoveLine,
  onClearAll,
  onAddItem,
  onContinue,
  taxTypes,
  focusTarget,
  ordersTodayCount,
  onShowOrdersToday,
}: Props) {
  const totals = orderTotals(lines, taxTypes, taxMode, 0);

  const canContinue =
    !!customer &&
    lines.length > 0 &&
    lines.every((l) => l.stock_code && l.quantity > 0);

  // Focus the Qty cell at `focusTarget.idx` whenever the parent signals an
  // add/merge. Scrolls the row into view too, so re-adding a stock that's
  // already in the draft takes you to its existing line.
  const tbodyRef = useRef<HTMLTableSectionElement>(null);
  const addItemWrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!focusTarget) return;
    requestAnimationFrame(() => {
      const row = tbodyRef.current?.children[focusTarget.idx] as
        | HTMLTableRowElement
        | undefined;
      const input = row?.querySelector<HTMLInputElement>("td[data-qty] input");
      if (!input) return;
      row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
  }, [focusTarget]);

  return (
    <div className="bg-white rounded border shadow-sm flex flex-col overflow-hidden h-full">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50 flex justify-between items-center">
        <div>
          <div className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
            Draft Order
          </div>
          {customer ? (
            <div className="text-sm text-gray-900">
              {customer.account} · {customer.name}
            </div>
          ) : (
            <div className="text-sm text-gray-400">No customer selected</div>
          )}
        </div>
        {lines.length > 0 && (
          <button
            onClick={onClearAll}
            className="text-xs text-gray-500 hover:text-red-600"
          >
            Clear all
          </button>
        )}
      </div>

      <div className="flex-1 overflow-auto p-2 min-h-0">
        {lines.length === 0 ? (
          <div className="text-center text-gray-400 text-xs py-8">
            Click <strong>+</strong> on any product in the history grid, or use
            the <strong>Add item</strong> box below, to build the order.
          </div>
        ) : (
          // table-layout: fixed enforces the TH `w-X` widths. In auto layout
          // short numeric content (Qty/Price/Total) lets the browser shrink
          // those columns, and the Item column greedily absorbs the leftover.
          <table className="w-full text-xs border-collapse" style={{ tableLayout: "fixed" }}>
            <thead>
              <tr className="text-gray-500 border-b bg-gray-50">
                <th className="text-left py-1 px-1 font-medium">Item</th>
                <th className="text-center py-1 px-1 font-medium w-[55px]">Pack</th>
                <th className="text-center py-1 px-1 font-medium w-[45px] whitespace-nowrap">Units</th>
                <th className="text-center py-0.5 px-1 font-medium w-[40px] leading-tight">
                  <div>Price</div>
                  <div className="text-[9px] font-normal text-gray-500">(excl)</div>
                </th>
                <th className="text-right py-1 px-1 font-medium w-16">Total</th>
                <th className="w-6"></th>
              </tr>
            </thead>
            <tbody
              ref={tbodyRef}
              onKeyDown={(e) => {
                if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
                const target = e.target as HTMLElement;
                if (!(target instanceof HTMLInputElement)) return;
                const row = target.closest("tr");
                const tbody = tbodyRef.current;
                if (!row || !tbody) return;
                const rows = Array.from(tbody.children) as HTMLTableRowElement[];
                const idx = rows.indexOf(row);
                const nextIdx = e.key === "ArrowDown" ? idx + 1 : idx - 1;
                const next = rows[nextIdx];

                // Past the last row? Jump down into the Add Item picker so the
                // rep can keep flowing with the keyboard.
                if (!next && e.key === "ArrowDown") {
                  const cell = target.closest("td");
                  if (cell?.matches("[data-qty]")) {
                    const addInput = addItemWrapRef.current?.querySelector<HTMLInputElement>(
                      "input"
                    );
                    if (addInput) {
                      e.preventDefault();
                      addInput.focus();
                    }
                  }
                  return;
                }
                if (!next) return;

                e.preventDefault();
                const nextQty = next.querySelector<HTMLInputElement>(
                  "td[data-qty] input"
                );
                if (!nextQty) return;
                nextQty.focus();
                nextQty.setSelectionRange(nextQty.value.length, nextQty.value.length);
              }}
            >
              {lines.map((line, i) => {
                const { excl } = lineAmounts(line, taxTypes, taxMode);
                const itemTitle = `${line.stock_code} — ${line.item_description || line.description}`;
                return (
                  <tr
                    key={i}
                    className="draft-row border-b border-gray-100 align-top cursor-text"
                    onClick={(e) => {
                      // Click anywhere in the row that isn't itself an interactive
                      // element → focus the Qty cell. Lets the rep tap the stock
                      // code / description to jump straight into editing qty.
                      const t = e.target as HTMLElement;
                      if (t.closest("input, button, select")) return;
                      const qty = e.currentTarget.querySelector<HTMLInputElement>(
                        "td[data-qty] input"
                      );
                      if (!qty) return;
                      qty.focus();
                      qty.setSelectionRange(qty.value.length, qty.value.length);
                    }}
                  >
                    <td className="py-1 px-1" title={itemTitle}>
                      <div className="font-medium text-gray-900 leading-tight truncate">
                        {line.stock_code}
                      </div>
                      <div className="text-gray-500 text-xs leading-tight truncate">
                        {line.item_description || line.description}
                      </div>
                    </td>
                    <td className="py-1 px-1 text-center text-gray-700 font-mono">
                      {line.pack_size > 1 ? `× ${line.pack_size}` : ""}
                    </td>
                    <td className="py-1 px-1" data-qty>
                      <div className="flex items-center gap-0.5">
                        {line.pack_size > 1 && (
                          <button
                            type="button"
                            onClick={() =>
                              onUpdateLine(i, { pack_override: !line.pack_override })
                            }
                            title={
                              line.pack_override
                                ? "Pack size override is ON — click to re-enable pack snapping"
                                : "Click to allow a non-pack-multiple qty"
                            }
                            className={`flex-shrink-0 p-0.5 rounded ${
                              line.pack_override
                                ? "text-amber-600 hover:bg-amber-50"
                                : "text-gray-300 hover:text-gray-500 hover:bg-gray-50"
                            }`}
                          >
                            {line.pack_override ? (
                              <Unlock className="w-3 h-3" />
                            ) : (
                              <Lock className="w-3 h-3" />
                            )}
                          </button>
                        )}
                        <NumberCell
                          value={line.quantity}
                          snapStep={
                            line.pack_override || line.pack_size <= 1
                              ? undefined
                              : line.pack_size
                          }
                          cursorToEnd
                          onChange={(v) => onUpdateLine(i, { quantity: v })}
                          className="w-full text-right border border-gray-200 rounded px-1 py-0.5 text-sm font-semibold focus:border-blue-500 focus:outline-none bg-white"
                          placeholder=""
                        />
                      </div>
                      {line.pack_size > 1 &&
                        line.quantity > 0 &&
                        line.quantity % line.pack_size !== 0 && (
                          <div className="text-[9px] text-amber-700 font-semibold leading-tight mt-0.5">
                            off-pack
                          </div>
                        )}
                    </td>
                    <td className="py-1 px-1">
                      <NumberCell
                        value={line.unit_price}
                        onChange={(v) =>
                          onUpdateLine(i, priceEditFromDisplay(v, line, taxTypes, taxMode))
                        }
                        disabled={line.price_locked}
                        title={
                          line.price_locked
                            ? `Price from ${line.price_source}`
                            : undefined
                        }
                        className={`w-full text-right border rounded px-1 py-0.5 text-xs focus:outline-none ${
                          line.price_locked
                            ? "bg-gray-100 border-gray-200 text-gray-700 cursor-not-allowed"
                            : line.unit_price <= 0
                              ? "bg-red-50 border-red-400 focus:border-red-500"
                              : "bg-white border-gray-200 focus:border-blue-500"
                        }`}
                        placeholder=""
                      />
                    </td>
                    <td className="py-1 px-1 text-right font-mono">
                      {fmtNumber(excl)}
                    </td>
                    <td className="py-1 px-1 text-center">
                      <button
                        onClick={() => onRemoveLine(i)}
                        className="text-gray-400 hover:text-red-600"
                        title="Remove"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="border-t border-gray-200 bg-white px-2 py-1.5 draft-add-item flex items-center gap-2">
        <span className="flex items-center gap-1 text-xs font-semibold text-blue-600 shrink-0">
          <Plus className="w-3.5 h-3.5" />
          Add item
        </span>
        <div
          ref={addItemWrapRef}
          className="flex-1 min-w-0"
          onKeyDown={(e) => {
            // ArrowUp from the Add Item input → focus the last Qty cell, so
            // the rep can flow back into editing the row they just created.
            if (e.key !== "ArrowUp") return;
            const tbody = tbodyRef.current;
            if (!tbody || tbody.children.length === 0) return;
            const lastRow = tbody.children[
              tbody.children.length - 1
            ] as HTMLTableRowElement;
            const lastQty = lastRow.querySelector<HTMLInputElement>(
              "td[data-qty] input"
            );
            if (!lastQty) return;
            e.preventDefault();
            lastQty.focus();
            lastQty.setSelectionRange(lastQty.value.length, lastQty.value.length);
          }}
        >
          <ItemPicker value="" onSelect={onAddItem} />
        </div>
      </div>

      <div className="border-t border-gray-200 bg-gray-50 px-3 py-2 text-sm">
        <div className="flex justify-between py-0.5">
          <span className="text-gray-600">Excl:</span>
          <span className="font-mono">{fmtCurrency(totals.excl)}</span>
        </div>
        <div className="flex justify-between py-0.5">
          <span className="text-gray-600">VAT:</span>
          <span className="font-mono">{fmtCurrency(totals.tax)}</span>
        </div>
        <div className="flex justify-between py-0.5 font-bold border-t border-gray-300 pt-1 mt-1">
          <span>Incl:</span>
          <span className="font-mono">{fmtCurrency(totals.incl)}</span>
        </div>
      </div>

      {customer && (
        <div className="border-t border-gray-200 px-2 py-2">
          <CreditStatus customer={customer} currentOrderIncl={totals.incl} compact />
          {ordersTodayCount !== undefined && ordersTodayCount > 0 && onShowOrdersToday && (
            <div className="mt-2 flex justify-end">
              <button
                onClick={onShowOrdersToday}
                className="inline-flex items-center gap-1 text-[10px] font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300 px-2 py-0.5 rounded-full hover:bg-emerald-200"
                title="View orders placed today for this customer"
              >
                <ListOrdered className="w-3 h-3" />
                {ordersTodayCount} today
              </button>
            </div>
          )}
        </div>
      )}

      <div className="border-t border-gray-200 p-2">
        <button
          onClick={onContinue}
          disabled={!canContinue}
          className={`w-full py-2 rounded font-semibold text-sm flex items-center justify-center gap-2 ${
            canContinue
              ? "bg-blue-600 hover:bg-blue-700 text-white"
              : "bg-gray-200 text-gray-500 cursor-not-allowed"
          }`}
        >
          Continue to Order
          <ArrowRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
