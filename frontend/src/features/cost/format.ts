/** Currency formatting for the cost panel (phase-46). */

/** Always two decimals, so a small spend is visible rather than rounded away to "₹0". */
export function inr(value: number): string {
  return `₹${value.toFixed(2)}`;
}
