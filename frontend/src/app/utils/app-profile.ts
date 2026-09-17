import { HttpErrorResponse } from "@angular/common/http";
import { ProfileResponse } from "../models/types";

export function isUnauthorizedHttpError(err: unknown): boolean {
  return err instanceof HttpErrorResponse && err.status === 401;
}

export function profileFallbackEn(): ProfileResponse {
  return { xp: 0, locale: "en", badges: [] };
}
