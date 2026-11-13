import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DraftDetail, DraftSaveRequest, DraftSummary } from "../api/types";
import type { LineRow } from "../components/LineItemsGrid";
import { resolvedPriceSource } from "./history";
import { today } from "./date";
import { extractErrorMsg } from "./format";
import { useFocusRefresh } from "./useFocusRefresh";
import { confirmDialog } from "../components/ConfirmDialog";

/** Fired after a draft is saved, deleted or finished (order placed). */
export const DRAFTS_CHANGED_EVENT = "drafts:changed";

export function notifyDraftsChanged() {
  window.dispatchEvent(new CustomEvent(DRAFTS_CHANGED_EVENT));
}

/** The order header a draft keeps (Sales Order Entry / the Order Assistant's
 *  finalize sheet). Order and due dates aren't kept: they are "today". */
export type DraftHeader = {
  externalOrder: string;
  description: string;
  documentDiscount: number;
  invoiceDate: string;
  deliveryDate: string;
};

/** What a draft stores: the screen's own state. Prices are fetched again on
 *  reopen (repriceLines), so the stored ones are never charged. */
export type DraftPayload = {
  version: 1;
  header?: DraftHeader | null;
  lines: LineRow[];
  /** Order Assistant: history categories already reviewed (Continue gate). */
  openedCategories?: string[];
};

export function readDraftPayload(d: DraftDetail): DraftPayload {
  const p = (d.payload ?? {}) as Partial<DraftPayload>;
  return {
    version: 1,
    header: p.header ?? null,
    lines: Array.isArray(p.lines) ? p.lines : [],
    openedCategories: Array.isArray(p.openedCategories) ? p.openedCategories : [],
  };
}

/** A saved date is kept unless it's already in the past. */
export function restoreDate(saved: string | undefined | null, fallback: string): string {
  return saved && saved >= today() ? saved : fallback;
}

/** Prices for the draft's lines as they are TODAY (customer deal / group deal /
 *  price list for the session date and the agent's DC). A line whose price
 *  can't be found is left unpriced ("None"), exactly like picking it fresh. */
export async function repriceLines(
  lines: LineRow[],
  account: string,
  asOf: string,
  warehouseCode: string | null,
): Promise<LineRow[]> {
  // A reopened draft is a new order: its lines come from the warehouse
  // being captured from NOW, like any line added today — not the one saved
  // with the draft (a draft saved on MAK, reopened on POL, ships from POL).
  if (warehouseCode) lines = lines.map((l) => (l.stock_code ? { ...l, warehouse_code: warehouseCode } : l));
  const codes = [...new Set(lines.filter((l) => l.stock_code).map((l) => l.stock_code))];
  if (codes.length === 0) return lines;
  const map = await api.getCustomerPrices(account, codes, asOf, warehouseCode);
  return lines.map((l) => {
    if (!l.stock_code) return l;
    const p = map[l.stock_code];
    const excl = p?.excl_price ?? 0;
    return {
      ...l,
      unit_price: excl,
      unit_price_incl: p?.incl_price ?? 0,
      price_source: resolvedPriceSource(l.stock_code, excl, p?.source ?? null),
      price_locked: excl > 0,
      is_fallback_warehouse: p?.is_fallback_warehouse ?? false,
      price_list_broken: p?.price_list_broken ?? false,
    };
  });
}

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export function draftSavedLabel(d: DraftSummary): string {
  return fmtWhen(d.created_at);
}

/** Save a draft. When the agent already has another draft for the customer,
 *  asks before replacing it. Returns null when they chose not to replace. */
export async function saveDraftWithConfirm(req: DraftSaveRequest): Promise<DraftSummary | null> {
  try {
    const saved = await api.saveDraft(req);
    notifyDraftsChanged();
    return saved;
  } catch (e: any) {
    const existing: DraftSummary | undefined = e?.response?.status === 409
      ? e.response.data?.detail?.existing
      : undefined;
    if (!existing) throw new Error(extractErrorMsg(e, "Could not save the draft"));
    const ok = await confirmDialog({
      title: "Replace your existing draft?",
      message:
        `You already have a draft for ${existing.customer_account} `
        + `(${existing.line_count} line${existing.line_count === 1 ? "" : "s"}, saved ${fmtWhen(existing.created_at)}). `
        + "Saving replaces it with this order.",
      confirmLabel: "Replace draft",
      cancelLabel: "Keep existing",
      tone: "danger",
    });
    if (!ok) return null;
    const saved = await api.saveDraft({ ...req, replace: true });
    notifyDraftsChanged();
    return saved;
  }
}

/**
 * The signed-in agent's drafts. PERFORMANCE.md: no polling. Fetched on
 * mount, on DRAFTS_CHANGED_EVENT (this agent saved / deleted / finished one),
 * and on window focus ONLY when the calendar day has changed since the last
 * fetch (so an app left open overnight still shows the next-day reminder).
 * Each fetch is one indexed read of a tiny table plus the expiry purge.
 */
export function useDrafts(): { drafts: DraftSummary[]; refresh: () => void; error: string | null } {
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const fetchedOn = useRef<string>("");
  const inflight = useRef(false);

  const refresh = useCallback(() => {
    if (inflight.current) return;
    inflight.current = true;
    fetchedOn.current = today();
    api
      .listDrafts()
      .then((d) => {
        setDrafts(d);
        setError(null);
      })
      .catch((e) => setError(extractErrorMsg(e, "Drafts are unavailable")))
      .finally(() => {
        inflight.current = false;
      });
  }, []);

  useEffect(() => {
    refresh();
    const onChanged = () => refresh();
    window.addEventListener(DRAFTS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(DRAFTS_CHANGED_EVENT, onChanged);
  }, [refresh]);
  // Back in the app on a new day: the next-day reminder must show.
  useFocusRefresh(
    useCallback(() => {
      if (fetchedOn.current !== today()) refresh();
    }, [refresh]),
  );

  return { drafts, refresh, error };
}
