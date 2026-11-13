import { useEffect, useRef, useState } from "react";
import { notifyReviewsChanged } from "../utils/usePendingReviewCount";
import {
  AlertTriangle, CheckCircle2, FileText, FileUp, Link2, RefreshCw, Send, X,
} from "lucide-react";
import { confirmDialog } from "./ConfirmDialog";
import { api } from "../api/client";
import type {
  CustomerDetail,
  ImportLineResult,
  ImportLineStatus,
  ImportReviewDetail,
  ImportStoreCheck,
  ImportValidation,
  InventoryItem,
  ParsedOrder,
} from "../api/types";
import { CustomerPicker } from "./CustomerPicker";
import { CreditStatus } from "./CreditStatus";
import { DateInput } from "./DateInput";
import { ItemPicker } from "./ItemPicker";
import { useAuth } from "../context/AuthContext";
import { nextBusinessDay, today } from "../utils/date";
import { extractErrorMsg, fmtCurrency, fmtNumber } from "../utils/format";

/**
 * Order Import — turn an emailed purchase order (PDF) into a sales order.
 *
 * 1. Pick the customer, choose the PDF, "Read order".
 * 2. The backend reads the PDF and checks it against Evolution: store code
 *    vs Client.ucARStoreCode, PO number not already captured, every line
 *    matched to a stock item, order price = Evolution price, totals agree.
 *    Crates (999000) are not charged: left off the order and the PO total.
 *    Prices (2026-11-10): same price, or the PO up to 5c above ours -> our
 *    price is charged and the agent can create the order. A bigger gap, or
 *    ours above the PO -> the agent submits the order for review; a
 *    supervisor sets the price per line (PO / ours / typed) and creates it.
 * 3. Lines that didn't match are mapped by picking our item. The mapping is
 *    saved (per corporate group) so the next order matches by itself.
 * 4. Confirm the dates, then Create Order. The backend re-runs every check
 *    and refuses if anything is still open.
 */

type Props = {
  onBack: () => void;
  /** Open an order submitted for review (supervisor: approve / reject). */
  reviewId?: number | null;
};

const STATUS_LABEL: Record<ImportLineStatus, string> = {
  OK: "OK",
  UNMATCHED: "Not mapped",
  ITEM_ISSUE: "Item issue",
  NO_PRICE: "No price",
  PRICE_MISMATCH: "Needs review",
  PACK_MISMATCH: "Pack differs",
  VAT_MISMATCH: "VAT differs",
  PARSE_ERROR: "Unreadable",
  EXCLUDED: "Not charged",
};

/** The PO number when the document doesn't print one (OK Foods): typed
 *  from the e-mail, applied on Enter / leaving the box, then re-checked
 *  (duplicate PO check). Required before the order can be created. */
function PoNumberInput({
  value, disabled, onChange,
}: { value: string; disabled: boolean; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => { setDraft(value); }, [value]);
  const apply = () => {
    const v = draft.trim().slice(0, 50);
    if (v !== value) onChange(v);
  };
  return (
    <div>
      <input
        className={`input h-7 w-48 font-mono text-sm ${value ? "" : "border-amber-500 bg-amber-50"}`}
        value={draft}
        disabled={disabled}
        maxLength={50}
        placeholder="Type the PO number"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={apply}
        onKeyDown={(e) => {
          if (e.key === "Enter") apply();
          if (e.key === "Escape") setDraft(value);
        }}
      />
      <div className="text-xs text-gray-500 mt-0.5">Not on the document: type it from the e-mail</div>
    </div>
  );
}

/** The pack the PO prints for a line (before any correction); 1 = singles. */
function printedPack(l: ImportLineResult): number {
  return l.doc_pack_printed ?? l.doc_pack_size ?? 1;
}

/** Pack sizes are whole numbers: "10", not "10,00". */
function fmtPack(n: number): string {
  return Number.isInteger(n) ? String(n) : fmtNumber(n);
}

const PACK_CAPTION =
  "mb-0.5 text-center text-[9px] font-semibold uppercase tracking-wide text-gray-500";

/** One small captioned box, styled like the price boxes. */
function PackBox({
  caption, value, tone, title,
}: { caption: string; value: React.ReactNode; tone: string; title?: string }) {
  return (
    <div title={title}>
      <div className={PACK_CAPTION}>{caption}</div>
      <div
        className={`h-6 min-w-[2.5rem] px-1.5 rounded border text-xs leading-none flex items-center justify-center ${tone}`}
      >
        {value}
      </div>
    </div>
  );
}

const TONE_PLAIN = "border-gray-200 bg-white text-gray-600";
const TONE_BAD = "border-red-300 bg-red-50 text-red-700 font-semibold";
const TONE_WARN = "border-amber-300 bg-amber-50 text-amber-800";
const TONE_FIXED = "border-blue-300 bg-blue-50 text-blue-700 font-semibold";

/** Units, the PO's packs x pack, and how that compares with OUR pack size
 *  (StkItem.ulIIPerCrate): PO and Ours side by side, like the prices.
 *  Only lines whose pack sizes don't agree can be fixed here. */
function QtyPackCell({ line: l }: { line: ImportLineResult }) {
  const ours = l.our_pack_size;
  const docPack = l.doc_pack_size ?? 1;
  const flagged = !!l.pack_conflict || l.pack_fix === "CORRECTED" || !!l.off_pack;

  let poTone = TONE_PLAIN;
  let oursTone = TONE_PLAIN;
  if (l.pack_blocks) poTone = oursTone = TONE_BAD;
  else if (l.pack_conflict) poTone = oursTone = TONE_WARN;
  else if (l.pack_fix === "CORRECTED") poTone = TONE_FIXED;
  if (!l.pack_conflict && l.off_pack) oursTone = TONE_WARN;

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="text-right">
        {l.qty_before_pack != null ? (
          <div
            className="whitespace-nowrap"
            title={`The PO orders ${fmtPack(l.qty_before_pack)} units: rounded down to whole crates of ${l.our_pack_size}`}
          >
            <span className="text-gray-400 line-through">{fmtNumber(l.qty_before_pack)}</span>
            <span className="mx-1 text-gray-400">→</span>
            <span className="font-semibold text-amber-800">{fmtNumber(l.qty)}</span>
          </div>
        ) : (
          <div>{fmtNumber(l.qty)}</div>
        )}
        {l.doc_pack_qty != null && l.doc_pack_size != null && (
          <div className="text-[11px] text-gray-500" title="Packs x pack size on the PO">
            {fmtPack(l.doc_pack_qty)} × {fmtPack(l.doc_pack_size)}
          </div>
        )}
      </div>
      {flagged && (
        <div className="flex items-end gap-1">
          <PackBox
            caption={l.pack_fix === "CORRECTED" ? "PO (fixed)" : "PO pack"}
            value={
              l.pack_fix === "CORRECTED"
                ? <>{fmtPack(printedPack(l))}<span className="mx-0.5">→</span>{fmtPack(docPack)}</>
                : fmtPack(docPack)
            }
            tone={poTone}
            title={
              l.pack_fix === "CORRECTED"
                ? `The PO prints ${fmtPack(printedPack(l))}; a supervisor corrected it to ${fmtPack(docPack)}`
                : l.pack_conflict
                  ? `Their case is ${fmtPack(docPack)}, ours is ${ours && ours > 1 ? ours : "not set"}: the units are rounded down to whole crates of ours`
                  : undefined
            }
          />
          <PackBox
            caption="Ours"
            value={ours && ours > 1 ? ours : "—"}
            tone={oursTone}
            title={
              l.off_pack && !l.pack_conflict
                ? `${fmtPack(l.qty)} units isn't whole crates of ${ours}: the order will show off-pack`
                : "Our pack size in Evolution"
            }
          />
        </div>
      )}
    </div>
  );
}

/** Lines with no product code (Spar) are matched on description + size:
 *  their item code is a fingerprint of that text, not something to show. */
function isDescriptionKey(code: string | null | undefined): boolean {
  return !!code && code.startsWith("D#");
}

function isoToDmy(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

export function OrderImportScreen({ onBack, reviewId = null }: Props) {
  const { sessionDate } = useAuth();
  const fileRef = useRef<HTMLInputElement>(null);

  const [customer, setCustomer] = useState<CustomerDetail | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [doc, setDoc] = useState<ParsedOrder | null>(null);
  const [result, setResult] = useState<ImportValidation | null>(null);

  const [orderDate, setOrderDate] = useState(today());
  const [deliveryDate, setDeliveryDate] = useState(sessionDate ?? nextBusinessDay());
  const [datesConfirmed, setDatesConfirmed] = useState(false);

  const [busy, setBusy] = useState<null | "reading" | "checking" | "creating">(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [createdOrderNo, setCreatedOrderNo] = useState<string | null>(null);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [editingLine, setEditingLine] = useState<number | null>(null);
  // Item just picked for a line, shown straight away while the mapping is
  // saved and the order re-checked (so the pick doesn't look ignored).
  const [pendingMap, setPendingMap] = useState<Record<number, InventoryItem>>({});
  // Set when the PO's store code belongs to a different customer.
  const [storePrompt, setStorePrompt] = useState<ImportStoreCheck | null>(null);
  // Supervisor prices (incl) per line_no. Kept in a ref so every re-check /
  // create sends the latest set. The server ignores them for agents.
  const overridesRef = useRef<Record<number, number>>({});
  function setOverrides(next: Record<number, number>) {
    overridesRef.current = next;
  }
  const [description, setDescription] = useState("Sales Order");
  const [review, setReview] = useState<ImportReviewDetail | null>(null);
  const [submitOpen, setSubmitOpen] = useState(false);
  const [submitNote, setSubmitNote] = useState("");
  const [submittedId, setSubmittedId] = useState<number | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectNote, setRejectNote] = useState("");
  const [rejected, setRejected] = useState(false);

  async function validate(account: string, document: ParsedOrder, delivery: string) {
    setBusy("checking");
    try {
      setResult(await api.validateImport(
        account, document, delivery, overridesRef.current, reviewId,
      ));
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Could not check the order"));
    } finally {
      setBusy(null);
    }
  }

  /** Store check first: a PO for another store is stopped before the order
   *  is checked or shown, and the user is offered the right customer. */
  async function proceed(account: string, document: ParsedOrder, delivery: string) {
    setBusy("checking");
    setResult(null);
    try {
      const check = await api.checkImportStore(account, document.store_code);
      if (check.store_check === "MISMATCH") {
        setStorePrompt(check);
        setBusy(null);
        return;
      }
      setStorePrompt(null);
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Could not check the store code"));
      setBusy(null);
      return;
    }
    await validate(account, document, delivery);
  }

  // Review mode: load what the agent submitted and re-check it now.
  useEffect(() => {
    if (reviewId == null) return;
    let alive = true;
    (async () => {
      setBusy("checking");
      try {
        const r = await api.getImportReview(reviewId);
        const cust = await api.getCustomer(r.customer_account);
        if (!alive) return;
        setReview(r);
        setCustomer(cust);
        setDoc(r.document);
        setOrderDate(r.order_date ?? today());
        const dd = r.delivery_date ?? nextBusinessDay();
        setDeliveryDate(dd);
        setDescription(r.description || "Sales Order");
        await validate(cust.account, r.document, dd);
      } catch (e) {
        if (alive) {
          setErrorMsg(extractErrorMsg(e, "Could not open the review"));
          setBusy(null);
        }
      }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewId]);

  async function onSelectCustomer(account: string) {
    setOverrides({});
    try {
      const detail = await api.getCustomer(account);
      setCustomer(detail);
      if (doc) await proceed(detail.account, doc, deliveryDate);
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Failed to load customer"));
    }
  }

  function cancelImport() {
    setStorePrompt(null);
    setDoc(null);
    setResult(null);
  }

  async function onRead() {
    if (!customer || !file) return;
    setBusy("reading");
    setErrorMsg(null);
    setResult(null);
    try {
      const parsed = await api.parseImportPdf(file);
      setDoc(parsed);
      setOverrides({});
      // Dates come off the PO; the user confirms (or changes) them.
      const od = parsed.order_date ?? today();
      const dd = parsed.delivery_from ?? sessionDate ?? nextBusinessDay();
      setOrderDate(od);
      setDeliveryDate(dd);
      setDatesConfirmed(false);
      await proceed(customer.account, parsed, dd);
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Could not read the order"));
      setBusy(null);
    }
  }

  function onDeliveryDateChange(iso: string) {
    setDeliveryDate(iso);
    setDatesConfirmed(false);
    // Prices / deals are resolved as of the delivery date — re-check.
    if (iso && customer && doc) validate(customer.account, doc, iso);
  }

  // "Already on the order" cue (same as Order Entry): the line flashes blue
  // and scrolls into view. flash.key remounts the row so the animation
  // restarts on repeat picks.
  // The row also shows `message` in a small box until it's dismissed or
  // 5 s pass (the blue flash itself fades in 2 s).
  const [flash, setFlash] = useState<{ line: number; key: number; message: string } | null>(null);
  const flashSeq = useRef(0);
  function flashLine(lineNo: number, message: string) {
    const key = ++flashSeq.current;
    setOnlyProblems(false);
    setFlash({ line: lineNo, key, message });
    requestAnimationFrame(() =>
      document
        .getElementById(`import-line-${lineNo}`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" }),
    );
    setTimeout(() => setFlash((f) => (f?.key === key ? null : f)), 5000);
  }

  async function mapLine(line: ImportLineResult, item: InventoryItem) {
    if (!customer || !doc) return;
    // Picked the item it's already on — nothing to save.
    if (line.stock_code && line.stock_code === item.code) {
      setEditingLine(null);
      return;
    }
    // Another line is already on this stock item: each of the customer's
    // items maps to its own stock item. Same cue as Order Entry — flash the
    // existing line instead of saving. (The server refuses it too.)
    const dup = result?.lines.find(
      (o) => o.line_no !== line.line_no && !o.excluded && o.stock_code === item.code,
    );
    if (dup) {
      setEditingLine(null);
      flashLine(dup.line_no, `${item.code} is already on this line. Choose a different item for line ${line.line_no}.`);
      return;
    }
    setPendingMap((m) => ({ ...m, [line.line_no]: item }));
    setBusy("checking");
    setEditingLine(null);
    try {
      await api.saveImportMapping({
        customer_account: customer.account,
        supplier_code: line.supplier_code,
        item_code: line.item_code,
        description: line.description,
        stock_code: item.code,
      });
      await validate(customer.account, doc, deliveryDate);
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Could not save the mapping"));
      setBusy(null);
    } finally {
      setPendingMap((m) => {
        const next = { ...m };
        delete next[line.line_no];
        return next;
      });
    }
  }

  async function removeMapping(line: ImportLineResult) {
    if (!customer || !doc || line.mapping_id == null) return;
    const theirs = line.supplier_code
      || (isDescriptionKey(line.item_code) ? `"${line.description}"` : line.item_code);
    const ok = await confirmDialog({
      title: "Remove this mapping?",
      message: (
        <>
          <div className="font-mono text-sm my-1">
            {theirs} → {line.stock_code}
          </div>
          <div className="text-sm text-gray-600">{line.description}</div>
          <div className="text-sm mt-2">
            Their code stops matching our item on every PO from this group. You can map it again from this line.
          </div>
        </>
      ),
      confirmLabel: "Remove mapping",
      tone: "danger",
    });
    if (!ok) return;
    setBusy("checking");
    try {
      await api.deleteImportMapping(line.mapping_id);
      await validate(customer.account, doc, deliveryDate);
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Could not remove the mapping"));
      setBusy(null);
    }
  }

  /** Supervisor sets (or clears, price = null) the price on a line. */
  function setLinePrice(line: ImportLineResult, price: number | null) {
    if (!customer || !doc) return;
    const next = { ...overridesRef.current };
    if (price == null) delete next[line.line_no];
    else next[line.line_no] = price;
    setOverrides(next);
    // The re-check returns the line with the price applied (and redraws).
    validate(customer.account, doc, deliveryDate);
  }

  function requestBody() {
    return {
      customer_account: customer!.account,
      document: doc!,
      order_date: orderDate,
      due_date: orderDate,
      invoice_date: deliveryDate,
      delivery_date: deliveryDate,
      dates_confirmed: datesConfirmed,
      description: description.trim() || "Sales Order",
      price_overrides: overridesRef.current,
      review_id: reviewId,
    };
  }

  async function onSubmitReview() {
    if (!customer || !doc || !result) return;
    setBusy("creating");
    try {
      const r = await api.submitImportReview({ ...requestBody(), note: submitNote.trim() || null });
      setSubmitOpen(false);
      setSubmittedId(r.id);
      notifyReviewsChanged();
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Could not submit the order for review"));
      validate(customer.account, doc, deliveryDate);
    } finally {
      setBusy(null);
    }
  }

  async function onReject() {
    if (reviewId == null || !rejectNote.trim()) return;
    setBusy("creating");
    try {
      await api.rejectImportReview(reviewId, rejectNote.trim());
      setRejectOpen(false);
      setRejected(true);
      notifyReviewsChanged();
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Could not reject the order"));
    } finally {
      setBusy(null);
    }
  }

  async function viewPo(id: number | null | undefined) {
    if (!id) return;
    try {
      await api.openImportDocument(id);
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Could not open the source document"));
    }
  }

  async function onCreate() {
    if (!customer || !doc || !result) return;
    setBusy("creating");
    setErrorMsg(null);
    try {
      const res = await api.createImportedOrder(requestBody());
      setCreatedOrderNo(res.order_no);
      if (reviewId != null) notifyReviewsChanged();
    } catch (e) {
      setErrorMsg(extractErrorMsg(e, "Failed to create the sales order"));
      // Something changed underneath (price, duplicate) — show it.
      validate(customer.account, doc, deliveryDate);
    } finally {
      setBusy(null);
    }
  }

  function finish() {
    setCreatedOrderNo(null);
    window.dispatchEvent(new CustomEvent("orders:mutated"));
    onBack();
  }

  const blocking = result?.issues.filter((i) => i.blocking) ?? [];
  const warnings = result?.issues.filter((i) => !i.blocking) ?? [];
  const needsAttention = (l: ImportLineResult) => l.status !== "OK" && l.status !== "EXCLUDED";
  const problemLines = result?.lines.filter(needsAttention) ?? [];
  const shownLines = (result?.lines ?? []).filter((l) => !onlyProblems || needsAttention(l));
  const priceDiffLines = result?.lines.filter((l) => l.price_differs && !l.excluded) ?? [];
  const canCreate = !!result?.can_create && datesConfirmed && !busy;
  const reviewMode = reviewId != null;
  const reviewOpen = reviewMode && review?.status === "PENDING";
  const reviewLines =
    result?.lines.filter((l) => l.needs_review && l.price_override == null) ?? [];
  // Agent: the only problem is price lines -> hand it to a supervisor.
  const canSubmit =
    !reviewMode && !!result?.can_submit_review && datesConfirmed && !busy;
  const poDocId = doc?.document_id ?? review?.document_id ?? null;

  return (
    <div className="screen">
      <div className="screen-header">
        <div className="screen-header-title">
          <button className="btn btn-ghost" onClick={onBack} title="Back to Sales Orders">
            ← Back
          </button>
          <h1>{reviewMode ? `Price review #${reviewId}` : "Import Order"}</h1>
        </div>
        {result && (
          <div className="screen-header-actions">
            {poDocId && (
              <button className="btn btn-secondary" onClick={() => viewPo(poDocId)}>
                <FileText className="w-4 h-4" /> Source document
              </button>
            )}
            <button
              className="btn btn-secondary"
              disabled={!!busy || !customer || !doc}
              onClick={() => customer && doc && validate(customer.account, doc, deliveryDate)}
              title="Run all the checks again (e.g. after a price was fixed in Evolution)"
            >
              <RefreshCw className="w-4 h-4" />
              {busy === "checking" ? "Checking…" : "Re-check"}
            </button>
          </div>
        )}
      </div>

      {/* Step 1: customer + file (review mode: who submitted it and why) */}
      <div className="two-col">
        <div className="two-col-stack">
          <section className="panel">
            <header className="panel-header">Customer</header>
            <div className="customer-row">
              <label>Customer</label>
              {reviewMode ? (
                <div className="text-sm py-1">
                  {customer ? `${customer.account} — ${customer.name}` : "…"}
                </div>
              ) : (
                <CustomerPicker
                  value={customer ? `${customer.account} — ${customer.name}` : ""}
                  onSelect={onSelectCustomer}
                  corporateOnly
                />
              )}
            </div>
            {customer && (
              <CreditStatus customer={customer} currentOrderIncl={result?.totals.order_incl ?? 0} />
            )}
          </section>
        </div>
        <div className="two-col-stack">
          {reviewMode ? (
            <section className="panel">
              <header className="panel-header">Submitted for review</header>
              <div className="grid-2 p-3 text-sm">
                <label>Status</label>
                <div className="font-semibold">
                  {review?.status ?? "…"}
                  {review?.order_num && ` — order ${review.order_num}`}
                </div>
                <label>By</label>
                <div>
                  {review?.submitted_by}
                  {review?.submitted_at && ` · ${review.submitted_at.slice(0, 16).replace("T", " ")}`}
                </div>
                <label>Agent's note</label>
                <div className="whitespace-pre-wrap">{review?.submit_note || "—"}</div>
                {review?.review_note && (
                  <>
                    <label>Supervisor</label>
                    <div className="whitespace-pre-wrap">
                      {review.reviewed_by}: {review.review_note}
                    </div>
                  </>
                )}
              </div>
            </section>
          ) : (
          <section className="panel">
            <header className="panel-header">Purchase order (PDF)</header>
            <div className="p-3 flex items-center gap-2 flex-wrap">
              <input
                ref={fileRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setDoc(null);
                  setResult(null);
                }}
              />
              <button className="btn btn-secondary" onClick={() => fileRef.current?.click()}>
                <FileUp className="w-4 h-4" />
                {file ? "Change file" : "Choose file"}
              </button>
              <span className="text-xs text-gray-700 truncate max-w-xs">
                {file?.name ?? "No file chosen"}
              </span>
              <button
                className="btn btn-primary ml-auto"
                disabled={!customer || !file || !!busy}
                onClick={onRead}
                title={!customer ? "Choose the customer first" : !file ? "Choose the PDF" : ""}
              >
                {busy === "reading" ? "Reading…" : "Read order"}
              </button>
            </div>
          </section>
          )}
        </div>
      </div>

      {result && doc && (
        <>
          {/* Step 2: header checks + dates */}
          <div className="two-col">
            <div className="two-col-stack">
              <section className="panel">
                <header className="panel-header">Order details</header>
                <div className="grid-2 p-3 text-sm">
                  <label>Retailer</label>
                  <div>{doc.retailer}</div>
                  <label>External order no.</label>
                  {!doc.order_no || doc.order_no_typed ? (
                    <PoNumberInput
                      value={doc.order_no}
                      disabled={!!busy || !!createdOrderNo || submittedId != null}
                      onChange={(v) => {
                        const next = { ...doc, order_no: v, order_no_typed: true };
                        setDoc(next);
                        if (customer) validate(customer.account, next, deliveryDate);
                      }}
                    />
                  ) : (
                    <div className="font-mono">{doc.order_no}</div>
                  )}
                  <label>Store</label>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono">{doc.store_code || "—"}</span>
                    {doc.store_name && <span className="text-gray-600">{doc.store_name}</span>}
                    <StoreBadge result={result} />
                  </div>
                  {result.store_check === "MISMATCH" && result.store_code_customers.length > 0 && (
                    <>
                      <label />
                      <div className="flex gap-1 flex-wrap">
                        {result.store_code_customers.map((c) => (
                          <button
                            key={c.account}
                            className="btn btn-secondary text-xs"
                            onClick={() => onSelectCustomer(c.account)}
                          >
                            Use {c.account} — {c.name}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                  <label>Delivery window</label>
                  <div>
                    {isoToDmy(doc.delivery_from)} – {isoToDmy(doc.delivery_to)}
                  </div>
                  <label>Prices checked as at</label>
                  <div>
                    {isoToDmy(result.price_date)} · warehouse {result.warehouse_code ?? "—"}
                  </div>
                </div>
              </section>
            </div>
            <div className="two-col-stack">
              <section className="panel">
                <header className="panel-header">Dates</header>
                <div className="grid-2 p-3 text-sm">
                  <label>Order date</label>
                  <DateInput
                    value={orderDate}
                    onChange={(iso) => { setOrderDate(iso); setDatesConfirmed(false); }}
                  />
                  <label>Delivery date</label>
                  <DateInput value={deliveryDate} onChange={onDeliveryDateChange} />
                  <label />
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={datesConfirmed}
                      onChange={(e) => setDatesConfirmed(e.target.checked)}
                    />
                    I've checked the dates
                  </label>
                </div>
              </section>
            </div>
          </div>

          {(blocking.length > 0 || warnings.length > 0) && (
            <section className="panel">
              <div className="p-3 space-y-1">
                {blocking.map((i, k) => (
                  <div key={`b${k}`} className="flex gap-2 text-sm text-red-700">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    {i.message}
                  </div>
                ))}
                {warnings.map((i, k) => (
                  <div key={`w${k}`} className="flex gap-2 text-sm text-amber-700">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    {i.message}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Step 3: lines */}
          <section className="panel">
            <header className="panel-header flex items-center">
              <span>
                Lines ({result.lines.length}
                {problemLines.length > 0 && `, ${problemLines.length} need attention`}
                {priceDiffLines.length > 0 &&
                  `, ${priceDiffLines.length} with a PO price different from ours`}
                {reviewLines.length > 0 && `, ${reviewLines.length} need a supervisor`})
              </span>
              <label className="ml-auto flex items-center gap-1 text-xs font-normal">
                <input
                  type="checkbox"
                  checked={onlyProblems}
                  onChange={(e) => setOnlyProblems(e.target.checked)}
                />
                Only lines needing attention
              </label>
            </header>
            <div className="overflow-x-auto">
              <table className="w-full text-xs border-collapse">
                <thead className="bg-gray-100">
                  <tr className="text-gray-700 border-b border-gray-300">
                    <th className="text-left px-2 py-1.5 font-semibold">#</th>
                    <th className="text-left px-2 py-1.5 font-semibold">Their code / item</th>
                    <th className="text-left px-2 py-1.5 font-semibold">Their description</th>
                    <th className="text-left px-2 py-1.5 font-semibold w-72">Our item</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Qty</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Order price incl</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Evolution incl</th>
                    <th className="text-left px-2 py-1.5 font-semibold">Charge incl</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Order line incl</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Our line incl</th>
                    <th className="text-left px-2 py-1.5 font-semibold">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {shownLines.map((l) => {
                    const pending = pendingMap[l.line_no];
                    const showPicker =
                      !pending &&
                      (editingLine === l.line_no ||
                        (l.status === "UNMATCHED" && editingLine === null));
                    return (
                      <tr
                        key={flash?.line === l.line_no ? `flash-${flash.key}` : l.line_no}
                        id={`import-line-${l.line_no}`}
                        className={`border-b border-gray-200 align-top ${
                          flash?.line === l.line_no ? "row-flash-blue " : ""
                        }${
                          l.excluded
                            ? "bg-gray-50 text-gray-400"
                            : l.status !== "OK"
                            ? "bg-red-50"
                            : l.price_differs
                            ? "bg-amber-50"
                            : ""
                        }`}
                      >
                        <td className="px-2 py-1.5 text-gray-500">{l.line_no}</td>
                        <td className="px-2 py-1.5 font-mono">
                          <div>{l.supplier_code || "—"}</div>
                          {isDescriptionKey(l.item_code) ? (
                            <div
                              className="font-sans text-[11px] text-gray-500"
                              title="No code on the order: matched on its description and size"
                            >
                              by description
                            </div>
                          ) : (
                            <div className="text-gray-500">{l.item_code}</div>
                          )}
                          {/* OK Foods: the barcode IS their item code; show it once. */}
                          {l.barcode && l.barcode !== l.item_code && (
                            <div className="text-gray-400" title="Barcode">{l.barcode}</div>
                          )}
                        </td>
                        <td className="px-2 py-1.5 text-gray-800">{l.description}</td>
                        <td className="px-2 py-1.5">
                          {pending ? (
                            <div className="text-gray-700">
                              <span className="font-mono font-semibold">{pending.code}</span>{" "}
                              {pending.description}
                              <span className="ml-1 text-[10px] text-gray-500">saving…</span>
                            </div>
                          ) : showPicker ? (
                            <div className="flex items-center gap-1">
                              <div className="flex-1">
                                <ItemPicker
                                  value={editingLine === l.line_no ? l.stock_code ?? "" : ""}
                                  onSelect={(item) => mapLine(l, item)}
                                  // "Change" / "Choose item": ready to type at once;
                                  // Escape or clicking away cancels.
                                  autoFocus={editingLine === l.line_no}
                                  onCancel={
                                    editingLine === l.line_no
                                      ? () => setEditingLine(null)
                                      : undefined
                                  }
                                />
                              </div>
                              {editingLine === l.line_no && (
                                <button
                                  className="text-gray-500 hover:text-gray-800"
                                  onClick={() => setEditingLine(null)}
                                  title="Cancel"
                                >
                                  <X className="w-3.5 h-3.5" />
                                </button>
                              )}
                            </div>
                          ) : l.stock_code ? (
                            <div className="flex items-start gap-1">
                              <div className="flex-1">
                                <span className="font-mono font-semibold">{l.stock_code}</span>{" "}
                                {l.stock_description}
                                {l.match_source === "MAPPING" && (
                                  <span
                                    className="ml-1 inline-flex items-center gap-0.5 rounded bg-blue-100 text-blue-700 px-1 text-[10px]"
                                    title="Matched through a saved mapping"
                                  >
                                    <Link2 className="w-3 h-3" /> mapped
                                  </span>
                                )}
                                {flash?.line === l.line_no && (
                                  <div className="relative mt-1.5 inline-flex items-start gap-1.5 rounded border border-blue-300 bg-white px-2 py-1 text-[11px] text-blue-900 shadow-md">
                                    <span className="absolute -top-1 left-3 h-2 w-2 rotate-45 border-l border-t border-blue-300 bg-white" />
                                    <span>{flash.message}</span>
                                    <button
                                      className="text-blue-400 hover:text-blue-800"
                                      onClick={() => setFlash(null)}
                                      title="Dismiss"
                                    >
                                      <X className="w-3 h-3" />
                                    </button>
                                  </div>
                                )}
                              </div>
                              <button
                                className="text-blue-700 hover:underline whitespace-nowrap"
                                onClick={() => setEditingLine(l.line_no)}
                                disabled={!!busy}
                                title="Map this line to a different stock item"
                              >
                                Change
                              </button>
                              {l.mapping_id != null && (
                                <button
                                  className="text-gray-500 hover:text-red-700"
                                  onClick={() => removeMapping(l)}
                                  disabled={!!busy}
                                  title="Remove the saved mapping"
                                >
                                  <X className="w-3.5 h-3.5" />
                                </button>
                              )}
                            </div>
                          ) : (
                            <button
                              className="text-blue-700 hover:underline"
                              onClick={() => setEditingLine(l.line_no)}
                            >
                              Choose item
                            </button>
                          )}
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          <QtyPackCell line={l} />
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          {/* Line-total layouts (Pick n Pay) can give a fraction of a cent. */}
                          {l.doc_unit_incl === 0 && l.doc_unit_excl > 0 ? (
                            <span className="text-gray-500" title="The PO prints excl only: incl is worked out once the item is chosen">—</span>
                          ) : Math.abs(l.doc_unit_incl * 100 - Math.round(l.doc_unit_incl * 100)) > 1e-6 ? (
                            <span title="Line total ÷ units on the PO">
                              {l.doc_unit_incl.toFixed(4).replace(".", ",")}
                            </span>
                          ) : (
                            fmtNumber(l.doc_unit_incl)
                          )}
                          {l.doc_unit_excl > 0 && (
                            <div className="text-gray-500">{l.doc_unit_excl.toFixed(4)} ex</div>
                          )}
                        </td>
                        <td
                          className={`px-2 py-1.5 text-right ${
                            l.status === "PRICE_MISMATCH" ? "text-red-700 font-semibold" : ""
                          }`}
                        >
                          {l.evo_unit_incl != null ? fmtNumber(l.evo_unit_incl) : "—"}
                          {l.evo_unit_excl != null && (
                            <div className="text-gray-500 font-normal">
                              {l.evo_unit_excl.toFixed(4)} ex
                            </div>
                          )}
                          {l.price_source && (
                            <div className="text-gray-500 font-normal">{l.price_source}</div>
                          )}
                          {l.price_list_broken ? (
                            <div
                              className="text-amber-700 font-semibold cursor-help"
                              title={`Price list error: stored incl ${fmtNumber(l.evo_list_incl ?? 0)} doesn't match excl + VAT. Evolution would charge the stored incl; this order charges the correct ${fmtNumber(l.evo_unit_incl ?? 0)}. You can still create the order — please ask for the price list to be fixed.`}
                            >
                              check price list (list {fmtNumber(l.evo_list_incl ?? 0)})
                            </div>
                          ) : (
                            l.evo_list_incl != null &&
                            l.evo_unit_incl != null &&
                            Math.abs(l.evo_list_incl - l.evo_unit_incl) >= 0.005 && (
                              <div
                                className="text-amber-700 font-normal"
                                title="The price list's stored incl doesn't match this line's VAT (e.g. a 0% VAT customer), so the order is charged excl + VAT instead."
                              >
                                list {fmtNumber(l.evo_list_incl)}
                              </div>
                            )
                          )}
                        </td>
                        <td className="px-2 py-1.5">
                          <ChargeCell
                            line={l}
                            canSetPrice={!!result.can_override_prices && (!reviewMode || reviewOpen)}
                            disabled={!!busy}
                            onSetPrice={setLinePrice}
                          />
                        </td>
                        <td className={`px-2 py-1.5 text-right ${l.excluded ? "line-through" : ""}`}>
                          {fmtNumber(l.doc_line_incl)}
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          {l.unit_incl != null ? fmtNumber(l.line_incl) : "—"}
                        </td>
                        <td className="px-2 py-1.5">
                          <StatusChip status={l.status} />
                          {l.price_differs && l.status === "OK" && (
                            <span className="ml-1 inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold bg-amber-100 text-amber-800 border-amber-200">
                              Price differs
                            </span>
                          )}
                          {l.issues.map((msg, k) => (
                            <div key={k} className="text-red-700 mt-0.5 max-w-xs">{msg}</div>
                          ))}
                          {l.notes.map((msg, k) => (
                            <div key={`n${k}`} className="text-gray-600 mt-0.5 max-w-xs">{msg}</div>
                          ))}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {/* Step 4: totals + create */}
          <section className="panel">
            <header className="panel-header">Totals</header>
            <div className="p-3">
              <table className="text-sm">
                <thead>
                  <tr className="text-gray-600">
                    <th />
                    <th className="text-right px-3 font-semibold">Qty</th>
                    <th className="text-right px-3 font-semibold">Excl</th>
                    <th className="text-right px-3 font-semibold">VAT</th>
                    <th className="text-right px-3 font-semibold">Incl</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="pr-3 text-gray-700">Purchase order</td>
                    <td className="text-right px-3">{fmtOpt(result.totals.doc_qty, fmtNumber)}</td>
                    <td className="text-right px-3">{fmtOpt(result.totals.doc_excl, fmtCurrency)}</td>
                    <td className="text-right px-3">{fmtOpt(result.totals.doc_vat, fmtCurrency)}</td>
                    <td
                      className="text-right px-3"
                      title={result.totals.doc_incl_from_lines
                        ? "The order prints excl only: its incl is each line's excl plus the item's VAT"
                        : undefined}
                    >
                      {fmtOpt(result.totals.doc_incl, fmtCurrency)}
                      {result.totals.doc_incl_from_lines && <span className="text-gray-400">*</span>}
                    </td>
                  </tr>
                  {result.totals.excluded_incl !== 0 && (
                    <tr className="text-gray-600">
                      <td className="pr-3">Less crates (not charged)</td>
                      <td className="text-right px-3">−{fmtNumber(result.totals.excluded_qty)}</td>
                      <td />
                      <td />
                      <td className="text-right px-3">−{fmtCurrency(result.totals.excluded_incl)}</td>
                    </tr>
                  )}
                  {(result.totals.pack_round_incl ?? 0) !== 0 && (
                    <tr className="text-gray-600">
                      <td
                        className="pr-3"
                        title="Their case size isn't ours: the units are rounded down to whole crates of ours"
                      >
                        Rounded down to our crates
                      </td>
                      <td className="text-right px-3">
                        {fmtNumber(result.lines.reduce(
                          (n, l) => n + (l.qty_before_pack != null ? l.qty - l.qty_before_pack : 0), 0,
                        ))}
                      </td>
                      <td />
                      <td />
                      <td className="text-right px-3">
                        {fmtCurrency(result.totals.pack_round_incl ?? 0)}
                      </td>
                    </tr>
                  )}
                  {result.totals.price_adjust_incl !== 0 && (
                    <tr className="text-gray-600">
                      <td className="pr-3">Lines not charged at the PO price</td>
                      <td />
                      <td />
                      <td />
                      <td className="text-right px-3">
                        {fmtCurrency(result.totals.price_adjust_incl)}
                      </td>
                    </tr>
                  )}
                  {result.totals.rounding_incl !== 0 && (
                    <tr className="text-gray-600">
                      <td
                        className="pr-3"
                        title="The PO's unit prices are fractions of a cent: we charge them rounded down to the cent, so never more than the PO"
                      >
                        Rounded down to the cent
                      </td>
                      <td />
                      <td />
                      <td />
                      <td className="text-right px-3">
                        {fmtCurrency(result.totals.rounding_incl)}
                      </td>
                    </tr>
                  )}
                  {result.totals.expected_incl != null &&
                    result.totals.expected_incl !== result.totals.doc_incl && (
                      <tr className="text-gray-800 border-t border-gray-200">
                        <td className="pr-3">Purchase order to match</td>
                        <td />
                        <td />
                        <td />
                        <td className="text-right px-3">
                          {fmtCurrency(result.totals.expected_incl)}
                        </td>
                      </tr>
                    )}
                  <tr>
                    <td className="pr-3 text-gray-700">Our order (charged prices)</td>
                    <td className="text-right px-3">{fmtNumber(result.totals.order_qty)}</td>
                    <td className="text-right px-3">{fmtCurrency(result.totals.order_excl)}</td>
                    <td className="text-right px-3">{fmtCurrency(result.totals.order_tax)}</td>
                    <td className="text-right px-3">{fmtCurrency(result.totals.order_incl)}</td>
                  </tr>
                  <tr
                    className={`font-semibold ${
                      result.totals.agrees ? "text-green-700" : "text-red-700"
                    }`}
                  >
                    <td className="pr-3">Difference</td>
                    <td />
                    <td />
                    <td />
                    <td className="text-right px-3">
                      {fmtOpt(result.totals.difference_incl, fmtCurrency)}
                    </td>
                  </tr>
                </tbody>
              </table>
              <div className="text-xs text-gray-600 mt-1">
                Totals must agree within {fmtCurrency(result.totals.tolerance)} (cent rounding on
                VAT lines). Prices are checked per line to the cent.
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="customer-row p-3">
              <label>Description</label>
              <input
                className="input"
                value={description}
                maxLength={40}
                disabled={reviewMode && !reviewOpen}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
          </section>

          <div className="action-bar">
            <span className="text-sm mr-3">
              {reviewMode && !reviewOpen ? (
                <span className="text-gray-700">This review is {review?.status.toLowerCase()}.</span>
              ) : result.can_create ? (
                datesConfirmed ? (
                  <span className="text-green-700 flex items-center gap-1">
                    <CheckCircle2 className="w-4 h-4" /> All checks passed
                  </span>
                ) : (
                  <span className="text-amber-700">Confirm the dates to continue</span>
                )
              ) : result.can_submit_review && !reviewMode ? (
                <span className="text-amber-700">
                  {reviewLines.length} line{reviewLines.length === 1 ? "" : "s"} need a supervisor
                  (price or pack size) — {datesConfirmed ? "submit the order for review" : "confirm the dates, then submit for review"}
                </span>
              ) : reviewMode && reviewLines.length > 0 ? (
                <span className="text-amber-700">Set the price / fix the pack size on the highlighted lines</span>
              ) : (
                <span className="text-red-700">
                  Resolve the items above before the order can be created
                </span>
              )}
            </span>
            {reviewOpen && (
              <button
                className="btn btn-secondary btn-large mr-3"
                disabled={!!busy || !result.can_override_prices}
                onClick={() => setRejectOpen(true)}
                title={result.can_override_prices ? undefined : "Only a supervisor can reject"}
              >
                Reject
              </button>
            )}
            {!reviewMode && result.can_submit_review ? (
              <button
                className="btn btn-primary btn-large"
                disabled={!canSubmit}
                onClick={() => setSubmitOpen(true)}
              >
                <Send className="w-4 h-4" /> Submit for review
              </button>
            ) : (
              (!reviewMode || reviewOpen) && (
                <button className="btn btn-primary btn-large" disabled={!canCreate} onClick={onCreate}>
                  {busy === "creating"
                    ? "Creating order…"
                    : reviewMode ? "Approve & create order" : "Create Order"}
                </button>
              )
            )}
          </div>
        </>
      )}

      {storePrompt && doc && (
        <div className="modal-backdrop">
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-icon modal-icon--err">
              <AlertTriangle className="w-7 h-7" />
            </div>
            <h2 className="modal-title">Wrong customer for this order</h2>
            <p className="modal-body">
              This order is for store <strong>{storePrompt.store_code}</strong>
              {doc.store_name ? ` (${doc.store_name})` : ""}, but{" "}
              <strong>{storePrompt.customer_account}</strong> — {storePrompt.customer_name} has
              store code <strong>{storePrompt.customer_store_code}</strong>.
              {storePrompt.store_code_customers.length === 0 &&
                ` No customer in Evolution has store code ${storePrompt.store_code}.`}
            </p>
            <div className="flex flex-col gap-2 items-stretch">
              {storePrompt.store_code_customers.map((c) => (
                <button
                  key={c.account}
                  className="btn btn-primary"
                  disabled={!!busy}
                  onClick={() => onSelectCustomer(c.account)}
                >
                  Switch to {c.account} — {c.name}
                </button>
              ))}
              <button className="btn btn-secondary" onClick={cancelImport}>
                Cancel import
              </button>
            </div>
          </div>
        </div>
      )}

      {submitOpen && (
        <div className="modal-backdrop" onClick={() => setSubmitOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">Submit for review</h2>
            <p className="modal-body">
              A supervisor will check the {reviewLines.length} line
              {reviewLines.length === 1 ? "" : "s"} whose PO price differs from
              ours, fix them and create the order.
            </p>
            <textarea
              className="input w-full"
              rows={3}
              maxLength={500}
              placeholder="Note for the supervisor (optional)"
              value={submitNote}
              onChange={(e) => setSubmitNote(e.target.value)}
            />
            <div className="flex gap-2 justify-end mt-3">
              <button className="btn btn-secondary" onClick={() => setSubmitOpen(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={!!busy} onClick={onSubmitReview}>
                {busy ? "Submitting…" : "Submit"}
              </button>
            </div>
          </div>
        </div>
      )}

      {rejectOpen && (
        <div className="modal-backdrop" onClick={() => setRejectOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">Reject this order</h2>
            <p className="modal-body">The agent will see your reason. No order is created.</p>
            <textarea
              className="input w-full"
              rows={3}
              maxLength={500}
              placeholder="Reason (required)"
              value={rejectNote}
              onChange={(e) => setRejectNote(e.target.value)}
            />
            <div className="flex gap-2 justify-end mt-3">
              <button className="btn btn-secondary" onClick={() => setRejectOpen(false)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={!!busy || !rejectNote.trim()}
                onClick={onReject}
              >
                Reject order
              </button>
            </div>
          </div>
        </div>
      )}

      {(submittedId != null || rejected) && (
        <div className="modal-backdrop" onClick={finish}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-icon">
              <CheckCircle2 className="w-7 h-7" />
            </div>
            <h2 className="modal-title">
              {rejected ? "Order rejected" : "Sent for review"}
            </h2>
            <p className="modal-body">
              {rejected
                ? `PO ${doc?.order_no} was rejected. The agent can see your reason.`
                : `PO ${doc?.order_no} is waiting for a supervisor (review #${submittedId}). ` +
                  "It is listed under Sales Orders → Price review."}
            </p>
            <button className="btn btn-primary" onClick={finish}>OK</button>
          </div>
        </div>
      )}

      {createdOrderNo && (
        <div className="modal-backdrop" onClick={finish}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-icon">
              <CheckCircle2 className="w-7 h-7" />
            </div>
            <h2 className="modal-title">Sales Order Created</h2>
            <p className="modal-body">
              Order {createdOrderNo} has been created in Evolution for PO {doc?.order_no}.
            </p>
            <button className="btn btn-primary" onClick={finish}>OK</button>
          </div>
        </div>
      )}

      {errorMsg && (
        <div className="modal-backdrop" onClick={() => setErrorMsg(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-icon modal-icon--err">
              <AlertTriangle className="w-7 h-7" />
            </div>
            <h2 className="modal-title">Import problem</h2>
            <p className="modal-body">{errorMsg}</p>
            <button className="btn btn-primary" onClick={() => setErrorMsg(null)}>OK</button>
          </div>
        </div>
      )}
    </div>
  );
}

function fmtOpt(v: number | null, fmt: (n: number) => string): string {
  return v == null ? "—" : fmt(v);
}

/**
 * What the line charges per unit (incl). Supervisors can set the price on a
 * line whose PO price differs from ours: PO, ours, or a typed price. A
 * price above the PO is allowed but marked as a claim risk.
 */
function ChargeCell({
  line,
  canSetPrice,
  disabled,
  onSetPrice,
}: {
  line: ImportLineResult;
  canSetPrice: boolean;
  disabled: boolean;
  onSetPrice: (line: ImportLineResult, price: number | null) => void;
}) {
  // Text being typed in the "Set" box; null = not editing.
  const [draft, setDraft] = useState<string | null>(null);
  const cancelled = useRef(false);
  if (line.excluded) return <span className="text-gray-400">not charged</span>;
  const charged = line.unit_incl;
  const editable =
    canSetPrice &&
    line.evo_unit_incl != null &&
    (line.price_differs || line.needs_review || line.price_override != null);

  if (!editable) {
    return (
      <span className={line.needs_review ? "font-semibold text-red-700" : "font-semibold"}>
        {charged != null ? fmtNumber(charged) : "—"}
        {line.price_auto && (
          <span
            className="ml-1 text-[10px] font-normal text-gray-500"
            title="Our price, charged automatically: the PO is at most 5c above it"
          >
            (ours)
          </span>
        )}
      </span>
    );
  }

  const set = line.price_override != null;
  const poSel = set && line.price_choice === "PO";
  const oursSel = (set && line.price_choice === "EVOLUTION") || (!set && line.price_auto);
  const typedSel = set && line.price_choice === "OVERRIDE";

  const box = (selected: boolean) =>
    `h-7 w-[4.5rem] rounded border text-xs leading-none transition-colors ${
      selected
        ? "border-blue-600 bg-blue-600 text-white font-semibold shadow-sm"
        : "border-gray-300 bg-white text-gray-700 hover:border-blue-400 hover:bg-blue-50"
    } disabled:opacity-50`;
  // Clicking the highlighted box again clears the choice.
  const pick = (selected: boolean, price: number) =>
    onSetPrice(line, selected && set ? null : price);

  const applyDraft = () => {
    if (draft == null) return;
    if (cancelled.current) {
      cancelled.current = false;
      setDraft(null);
      return;
    }
    const v = Number(draft.replace(",", ".").trim());
    setDraft(null);
    if (draft.trim() === "") {
      if (typedSel) onSetPrice(line, null);
    } else if (Number.isFinite(v) && v > 0) {
      onSetPrice(line, Math.round(v * 100) / 100);
    }
  };

  // Small caption above each box; the box holds only the price, centred.
  const caption = (text: string) => (
    <div className="mb-0.5 text-center text-[9px] font-semibold uppercase tracking-wide text-gray-500">
      {text}
    </div>
  );

  return (
    <div className="flex gap-1 justify-end">
      <div>
        {caption("PO")}
        <button
          className={`${box(poSel)} flex items-center justify-center`}
          disabled={disabled}
          onClick={() => pick(poSel, line.doc_unit_incl)}
          title={poSel ? "Charging the PO price (click to clear)" : "Charge the PO price"}
        >
          {fmtNumber(line.doc_unit_incl)}
        </button>
      </div>
      <div>
        {caption("Ours")}
        <button
          className={`${box(oursSel)} flex items-center justify-center`}
          disabled={disabled}
          onClick={() => pick(oursSel, line.evo_unit_incl!)}
          title={oursSel ? "Charging our price" : "Charge our Evolution price"}
        >
          {fmtNumber(line.evo_unit_incl!)}
        </button>
      </div>
      <div>
        {caption("Set")}
        <input
          className={`${box(typedSel && draft == null)} block text-center outline-none focus:border-blue-500 ${
            typedSel && draft == null ? "placeholder-blue-200" : "placeholder-gray-400"
          }`}
          title="Type a price (incl) and press Enter; empty it to clear"
          placeholder="—"
          inputMode="decimal"
          disabled={disabled}
          value={draft ?? (typedSel && charged != null ? charged.toFixed(2) : "")}
          onFocus={(e) => {
            setDraft(typedSel && charged != null ? charged.toFixed(2) : "");
            e.currentTarget.select();
          }}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") {
              cancelled.current = true;
              e.currentTarget.blur();
            }
          }}
          onBlur={applyDraft}
        />
      </div>
    </div>
  );
}

function StatusChip({ status }: { status: ImportLineStatus }) {
  const cls =
    status === "OK"
      ? "bg-green-100 text-green-800 border-green-200"
      : status === "EXCLUDED"
      ? "bg-gray-100 text-gray-600 border-gray-200"
      : "bg-red-100 text-red-800 border-red-200";
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

function StoreBadge({ result }: { result: ImportValidation }) {
  if (result.store_check === "MATCH") {
    return (
      <span className="rounded bg-green-100 text-green-800 px-1.5 text-[10px] font-semibold">
        matches {result.customer_account}
      </span>
    );
  }
  if (result.store_check === "MISMATCH") {
    return (
      <span className="rounded bg-red-100 text-red-800 px-1.5 text-[10px] font-semibold">
        {result.customer_account} is {result.customer_store_code}
      </span>
    );
  }
  return (
    <span className="rounded bg-amber-100 text-amber-800 px-1.5 text-[10px] font-semibold">
      not verified
    </span>
  );
}
