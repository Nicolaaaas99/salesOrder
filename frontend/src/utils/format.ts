const numberFmt = new Intl.NumberFormat("en-ZA", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export const fmtNumber = (n: number) => numberFmt.format(n);
export const fmtCurrency = (n: number) => `R ${numberFmt.format(n)}`;
