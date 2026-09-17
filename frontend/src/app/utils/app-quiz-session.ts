import { normalizeDocumentId } from "./document-id";

export function resolveActiveQuizDocumentId(
  activeDocumentId: string | null,
  persistedDocumentId: string | null | undefined
): string | null {
  const active = normalizeDocumentId(activeDocumentId);
  if (active) {
    return active;
  }
  return normalizeDocumentId(persistedDocumentId ?? null);
}
