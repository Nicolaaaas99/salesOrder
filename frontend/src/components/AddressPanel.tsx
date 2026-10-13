import { useState } from "react";
import type { CustomerDetail } from "../api/types";

type Props = { customer: CustomerDetail | null };

type Tab = "delivery" | "postal" | "contact";

export function AddressPanel({ customer }: Props) {
  const [tab, setTab] = useState<Tab>("delivery");

  return (
    <section className="panel">
      <header className="panel-header panel-header--accent">Address</header>

      <div className="tabs">
        <button className={tab === "delivery" ? "tab active" : "tab"} onClick={() => setTab("delivery")}>Delivery Address</button>
        <button className={tab === "postal" ? "tab active" : "tab"} onClick={() => setTab("postal")}>Postal Address</button>
        <button className={tab === "contact" ? "tab active" : "tab"} onClick={() => setTab("contact")}>Contact</button>
      </div>

      <div className="tab-body">
        {!customer && <div className="muted">Select a customer to see address details.</div>}

        {customer && tab === "delivery" && (
          <pre className="address-block">{customer.delivery_address || "—"}</pre>
        )}

        {customer && tab === "postal" && (
          <pre className="address-block">{customer.postal_address || "—"}</pre>
        )}

        {customer && tab === "contact" && (
          <div className="contact-block">
            <div><span className="label">Name:</span> {customer.contact_name || "—"}</div>
            <div><span className="label">Tel:</span> {customer.contact_tel || "—"}</div>
            <div><span className="label">Email:</span> {customer.contact_email || "—"}</div>
          </div>
        )}
      </div>
    </section>
  );
}
