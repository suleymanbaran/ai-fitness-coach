export const TOTAL_BURN_FACTOR = 0.8;

export function adjustedTotalBurn(totalKcal: number | null): number | null {
  return totalKcal == null ? null : Math.round(totalKcal * TOTAL_BURN_FACTOR);
}
