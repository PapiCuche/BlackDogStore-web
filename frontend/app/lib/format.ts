export function formatMoney(value: number | string) {
  const amount = Number(value);
  if (Number.isNaN(amount)) return "0.00";
  return amount.toFixed(2);
}
