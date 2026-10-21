import { useEffect, useRef, useState } from "react";
import { User, Phone, Mail, Pencil, Check, X } from "lucide-react";
import type { CustomerDetail } from "../api/types";
import { api } from "../api/client";

type Props = {
  customer: CustomerDetail;
  /** Called with the server-returned, re-fetched detail after a successful
   *  save so the parent's state (credit area, etc.) stays in sync. */
  onUpdated: (updated: CustomerDetail) => void;
};

/**
 * Compact contact strip shown under the route/customer pickers. Click the
 * pencil to edit the contact person and the two telephone numbers; email
 * is read-only (not part of the Client.Contact_Person / Telephone(2) write
 * path). Saves hit PATCH /lookups/customers/{account}/contact.
 */
export function CustomerContactStrip({ customer, onUpdated }: Props) {
  const [isEditing, setIsEditing] = useState(false);
  const [name, setName] = useState(customer.contact_name);
  const [tel, setTel] = useState(customer.contact_tel);
  const [tel2, setTel2] = useState(customer.contact_tel2);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Reset local draft whenever the parent swaps to a different customer so the
  // strip doesn't carry stale values across selections.
  useEffect(() => {
    setName(customer.contact_name);
    setTel(customer.contact_tel);
    setTel2(customer.contact_tel2);
    setIsEditing(false);
    setError(null);
  }, [customer.account]);

  // Auto-focus the first input when flipping into edit mode.
  useEffect(() => {
    if (isEditing) nameInputRef.current?.focus();
  }, [isEditing]);

  async function save() {
    if (isSaving) return;
    setIsSaving(true);
    setError(null);
    try {
      const updated = await api.updateCustomerContact(customer.account, {
        contact_name: name,
        contact_tel: tel,
        contact_tel2: tel2,
      });
      onUpdated(updated);
      setIsEditing(false);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Failed to save contact");
    } finally {
      setIsSaving(false);
    }
  }

  function cancel() {
    setName(customer.contact_name);
    setTel(customer.contact_tel);
    setTel2(customer.contact_tel2);
    setError(null);
    setIsEditing(false);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      save();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    }
  }

  if (isEditing) {
    return (
      <div className="bg-white rounded border px-4 py-2 shadow-sm flex items-center gap-3 text-sm flex-shrink-0">
        {/* Save / cancel sit on the left — same spot the pencil was — so the
            rep's eye doesn't have to travel to the far side of the strip to
            commit the edit. */}
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            type="button"
            onClick={save}
            disabled={isSaving}
            title="Save (Enter)"
            className="flex items-center justify-center w-6 h-6 rounded text-emerald-700 hover:bg-emerald-50 hover:text-emerald-900 disabled:text-gray-400"
          >
            <Check className="w-4 h-4" strokeWidth={3} />
          </button>
          <button
            type="button"
            onClick={cancel}
            disabled={isSaving}
            title="Cancel (Esc)"
            className="flex items-center justify-center w-6 h-6 rounded text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:text-gray-300"
          >
            <X className="w-4 h-4" strokeWidth={3} />
          </button>
        </div>
        <span className="flex items-center gap-1.5 min-w-0 flex-1">
          <User className="w-4 h-4 text-gray-400 flex-shrink-0" />
          <input
            ref={nameInputRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Contact person"
            className="border border-gray-300 rounded px-2 py-1 text-sm w-full focus:border-blue-500 focus:outline-none"
          />
        </span>
        <span className="flex items-center gap-1.5 min-w-0 flex-1">
          <Phone className="w-4 h-4 text-gray-400 flex-shrink-0" />
          <input
            value={tel}
            onChange={(e) => setTel(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Telephone"
            className="border border-gray-300 rounded px-2 py-1 text-sm font-mono w-full focus:border-blue-500 focus:outline-none"
          />
        </span>
        <span className="flex items-center gap-1.5 min-w-0 flex-1">
          <Phone className="w-4 h-4 text-gray-400 flex-shrink-0" />
          <input
            value={tel2}
            onChange={(e) => setTel2(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Telephone 2"
            className="border border-gray-300 rounded px-2 py-1 text-sm font-mono w-full focus:border-blue-500 focus:outline-none"
          />
        </span>
        <span className="flex items-center gap-1.5 text-gray-700 min-w-0 flex-1">
          <Mail className="w-4 h-4 text-gray-400 flex-shrink-0" />
          <span className="truncate text-gray-400" title="Email is not editable here">
            {customer.contact_email || "—"}
          </span>
        </span>
        {error && (
          <span className="text-red-600 truncate text-sm" title={error}>
            {error}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="bg-white rounded border px-4 py-2 shadow-sm flex items-center gap-5 text-sm flex-shrink-0">
      <button
        type="button"
        onClick={() => setIsEditing(true)}
        title="Edit contact person & phones"
        className="flex-shrink-0 text-gray-400 hover:text-blue-600"
      >
        <Pencil className="w-4 h-4" />
      </button>
      <span className="flex items-center gap-1.5 text-gray-700 min-w-0">
        <User className="w-4 h-4 text-gray-400 flex-shrink-0" />
        <span className="font-semibold truncate" title={customer.contact_name || "—"}>
          {customer.contact_name || "—"}
        </span>
      </span>
      <span className="flex items-center gap-1.5 text-gray-700 min-w-0">
        <Phone className="w-4 h-4 text-gray-400 flex-shrink-0" />
        {customer.contact_tel ? (
          <a
            href={`tel:${customer.contact_tel}`}
            className="font-mono hover:text-blue-600 truncate"
            title={`Phone 1: ${customer.contact_tel}`}
          >
            {customer.contact_tel}
          </a>
        ) : (
          <span className="text-gray-400">—</span>
        )}
      </span>
      <span className="flex items-center gap-1.5 text-gray-700 min-w-0">
        <Phone className="w-4 h-4 text-gray-400 flex-shrink-0" />
        {customer.contact_tel2 ? (
          <a
            href={`tel:${customer.contact_tel2}`}
            className="font-mono hover:text-blue-600 truncate"
            title={`Phone 2: ${customer.contact_tel2}`}
          >
            {customer.contact_tel2}
          </a>
        ) : (
          <span className="text-gray-400">—</span>
        )}
      </span>
      <span className="flex items-center gap-1.5 text-gray-700 min-w-0 flex-1">
        <Mail className="w-4 h-4 text-gray-400 flex-shrink-0" />
        {customer.contact_email ? (
          <a
            href={`mailto:${customer.contact_email}`}
            className="hover:text-blue-600 truncate"
            title={customer.contact_email}
          >
            {customer.contact_email}
          </a>
        ) : (
          <span className="text-gray-400">—</span>
        )}
      </span>
    </div>
  );
}
