const numberFmt = new Intl.NumberFormat("en-ZA", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export const fmtNumber = (n: number) => numberFmt.format(n);
export const fmtCurrency = (n: number) => `R ${numberFmt.format(n)}`;

/** Pull a user-friendly message out of an axios error. Handles FastAPI's
 *  shape where 422 responses carry `detail` as an array of validator-error
 *  objects — otherwise we end up rendering "[object Object]". */
export function extractErrorMsg(err: any, fallback = "Unknown error"): string {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc)
          ? d.loc.filter((p: any) => p !== "body").join(".")
          : "";
        const msg = d?.msg ?? "invalid value";
        return loc ? `${loc}: ${msg}` : msg;
      })
      .join("; ");
  }
  if (err?.message) return String(err.message);
  return fallback;
}
