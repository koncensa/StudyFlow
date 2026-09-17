/** True when the id is a real signed-in account (not 0 / guest default). */
export function isValidStudyflowUserId(userId: number | null | undefined): userId is number {
  return typeof userId === "number" && Number.isFinite(userId) && userId >= 1;
}
