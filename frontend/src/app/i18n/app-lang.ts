/** UI + API locale codes we support today (expand later). */
export type AppLang = "en" | "tr";

export function normalizeAppLang(raw: string | null | undefined): AppLang {
  const v = (raw || "en").trim().toLowerCase();
  return v === "tr" ? "tr" : "en";
}
