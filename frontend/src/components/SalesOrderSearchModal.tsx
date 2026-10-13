import { useState } from "react";
import { api } from "../api/client";
import type { SalesOrderSearchResult } from "../api/types";
import { CustomerPicker } from "./CustomerPicker";
import { DateInput } from "./DateInput";
import { fmtCurrency } from "../utils/format";

type Props = {
  onClose: () => void;
  onSelect: (order: SalesOrderSearchResult) => void;
};

function fmtDmy(iso: string | null): string {
  if (!iso) return "";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

export function SalesOrderSearchModal({ onClose, onSelect }: Props) {
  const [account, setAccount] = useState("");
  const [orderNo, setOrderNo] = useState("");
  const [extOrder, setExtOrder] = useState("");
  const [description, setDescription] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const [results, setResults] = useState<SalesOrderSearchResult[] | null>(null);
  const [selected, setSelected] = useState<SalesOrderSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function runSearch() {
    setLoading(true);
    setErrorMsg(null);
    try {
      const data = await api.searchSalesOrders({
        account: account || undefined,
        order_no: orderNo || undefined,
        ext_order: extOrder || undefined,
        description: description || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
      setResults(data);
      setSelected(null);
    } catch (e: any) {
      setErrorMsg(e?.response?.data?.detail ?? e?.message ?? "Search failed");
      setResults([]);
    } finally {
      setLoading(false);
    }
  }

  function clearFilters() {
    setAccount("");
    setOrderNo("");
    setExtOrder("");
    setDescription("");
    setDateFrom("");
    setDateTo("");
    setResults(null);
    setSelected(null);
    setErrorMsg(null);
  }

  const actionLabel = selected
    ? (selected.is_archived ? "Load as Template" : "Open")
    : "Open";

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        className="modal modal--wide"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="modal-title" style={{ margin: 0 }}>Open Sales Order</h2>
          <button className="modal-close" onClick={onClose} title="Close">×</button>
        </div>

        <div className="search-filters">
          <label>Account</label>
          <CustomerPicker
            value={account}
            onChange={setAccount}
            onSelect={setAccount}
            placeholder="Account code or name"
            onEnter={runSearch}
          />

          <label>Order Number</label>
          <input
            className="input"
            value={orderNo}
            onChange={(e) => setOrderNo(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }}
          />

          <label>External Order</label>
          <input
            className="input"
            value={extOrder}
            onChange={(e) => setExtOrder(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }}
          />

          <label>Description</label>
          <input
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }}
          />

          <label>Date From</label>
          <DateInput value={dateFrom} onChange={setDateFrom} />

          <label>Date To</label>
          <DateInput value={dateTo} onChange={setDateTo} />
        </div>

        <div className="search-actions">
          <button className="btn btn-secondary" onClick={clearFilters}>Clear</button>
          <button className="btn btn-primary" onClick={runSearch} disabled={loading}>
            {loading ? "Searching…" : "Find"}
          </button>
        </div>

        <div className="search-results">
          {errorMsg && <div className="banner banner-err">{errorMsg}</div>}
          {results === null && !loading && (
            <div className="search-empty">Enter filters (optional) and click Find.</div>
          )}
          {results !== null && results.length === 0 && !loading && (
            <div className="search-empty">No sales orders match.</div>
          )}
          {results !== null && results.length > 0 && (
            <table className="line-grid search-grid">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Customer</th>
                  <th>Date</th>
                  <th>Order Number</th>
                  <th>External Order</th>
                  <th>Description</th>
                  <th style={{ textAlign: "right" }}>Total Incl</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr
                    key={r.order_no}
                    className={
                      "search-row" + (selected?.order_no === r.order_no ? " search-row--selected" : "")
                    }
                    onClick={() => setSelected(r)}
                    onDoubleClick={() => onSelect(r)}
                  >
                    <td>{r.customer_account}</td>
                    <td className="ellipsis" title={r.customer_name}>{r.customer_name}</td>
                    <td>{fmtDmy(r.invoice_date)}</td>
                    <td>{r.order_no}</td>
                    <td>{r.external_order}</td>
                    <td className="ellipsis" title={r.description}>{r.description}</td>
                    <td className="num">{fmtCurrency(r.total_incl)}</td>
                    <td>
                      <span
                        className={
                          "state-badge " +
                          (r.is_archived ? "state-badge--archived" : "state-badge--unprocessed")
                        }
                      >
                        {r.doc_state_label}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="search-footer">
          <span className="muted">
            {selected
              ? `Selected: ${selected.order_no} (${selected.doc_state_label})`
              : "Select an order below, then click Open or Load as Template."}
          </span>
          <div className="search-footer-actions">
            <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button
              className="btn btn-primary"
              onClick={() => selected && onSelect(selected)}
              disabled={!selected}
            >
              {actionLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
