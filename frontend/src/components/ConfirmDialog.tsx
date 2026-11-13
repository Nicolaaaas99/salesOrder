import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, HelpCircle } from "lucide-react";

/** In-app replacement for window.confirm:
 *
 *    if (await confirmDialog({ title: "Delete draft?", confirmLabel: "Delete", tone: "danger" })) ...
 *
 *  Rendered by <ConfirmDialogHost /> (mounted once in App.tsx), on top of
 *  any other modal. Falls back to window.confirm if no host is mounted. */
export type ConfirmOptions = {
  title: string;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** danger: red confirm button + warning icon (deleting / replacing). */
  tone?: "danger" | "primary";
};

type Request = ConfirmOptions & { resolve: (ok: boolean) => void };

let show: ((req: Request) => void) | null = null;

export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  if (!show) return Promise.resolve(window.confirm(opts.title));
  return new Promise((resolve) => show!({ ...opts, resolve }));
}

export function ConfirmDialogHost() {
  const [req, setReq] = useState<Request | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    show = (r) => setReq(r);
    return () => {
      show = null;
    };
  }, []);
  useEffect(() => {
    if (req) confirmRef.current?.focus();
  }, [req]);

  if (!req) return null;
  const close = (ok: boolean) => {
    req.resolve(ok);
    setReq(null);
  };
  const danger = req.tone === "danger";

  return (
    <div className="modal-backdrop" style={{ zIndex: 4000 }} onClick={() => close(false)}>
      <div
        className="modal"
        style={{ maxWidth: 400 }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") close(false);
        }}
      >
        <div className={`modal-icon ${danger ? "modal-icon--err" : ""}`} style={{ width: 44, height: 44 }}>
          {danger ? <AlertTriangle className="w-5 h-5" /> : <HelpCircle className="w-5 h-5" />}
        </div>
        <h2 className="modal-title">{req.title}</h2>
        {req.message && <div className="modal-body">{req.message}</div>}
        <div className="flex gap-2 justify-center mt-2">
          <button className="btn btn-secondary" onClick={() => close(false)}>
            {req.cancelLabel ?? "Cancel"}
          </button>
          <button
            ref={confirmRef}
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            onClick={() => close(true)}
          >
            {req.confirmLabel ?? "OK"}
          </button>
        </div>
      </div>
    </div>
  );
}
