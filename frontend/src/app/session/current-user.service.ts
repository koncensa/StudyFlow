import { Injectable } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { BehaviorSubject, Observable, throwError } from "rxjs";
import { catchError, tap } from "rxjs/operators";

import { environment } from "../../environments/environment";
import { STUDYFLOW_EMAIL_KEY, STUDYFLOW_TOKEN_KEY, STUDYFLOW_USER_ID_KEY, STUDYFLOW_USERNAME_KEY } from "./local-storage-keys";

/** Response body from POST /auth/login and POST /auth/register */
export interface SessionTokenResponse {
  access_token: string;
  token_type: string;
  user_id: number;
  email: string;
  username?: string | null;
}

@Injectable({ providedIn: "root" })
export class CurrentUserService {
  private readonly apiBaseUrl = environment.apiBaseUrl;
  private readonly userIdSubject = new BehaviorSubject<number | null>(this.readInitialUserId());

  /** Emits `null` when logged out. */
  readonly userId$ = this.userIdSubject.asObservable();

  constructor(private http: HttpClient) {}

  /** Numeric id when logged in; `null` when logged out. */
  get userId(): number | null {
    return this.userIdSubject.value;
  }

  get email(): string | null {
    return localStorage.getItem(STUDYFLOW_EMAIL_KEY);
  }

  get username(): string | null {
    const raw = localStorage.getItem(STUDYFLOW_USERNAME_KEY);
    const normalized = (raw || "").trim();
    return normalized || null;
  }

  get displayName(): string | null {
    return this.username || this.email;
  }

  hasSession(): boolean {
    return !!localStorage.getItem(STUDYFLOW_TOKEN_KEY);
  }

  /**
   * Creates the account only; does not store a session.
   * User should sign in via `login()` afterward.
   */
  register(email: string, password: string, fullName?: string): Observable<SessionTokenResponse> {
    const body: { email: string; password: string; full_name?: string } = { email, password };
    if (fullName) {
      body.full_name = fullName;
    }
    return this.http.post<SessionTokenResponse>(`${this.apiBaseUrl}/auth/register`, body);
  }

  login(email: string, password: string): Observable<SessionTokenResponse> {
    return this.http
      .post<SessionTokenResponse>(`${this.apiBaseUrl}/auth/login`, { email, password })
      .pipe(tap((r) => this.saveSession(r)));
  }

  logout(): void {
    localStorage.removeItem(STUDYFLOW_TOKEN_KEY);
    localStorage.removeItem(STUDYFLOW_USER_ID_KEY);
    localStorage.removeItem(STUDYFLOW_EMAIL_KEY);
    localStorage.removeItem(STUDYFLOW_USERNAME_KEY);
    this.userIdSubject.next(null);
  }

  updateSessionIdentity(identity: { email?: string | null; username?: string | null }): void {
    if (identity.email != null) {
      localStorage.setItem(STUDYFLOW_EMAIL_KEY, identity.email);
    }
    if (identity.username == null || !identity.username.trim()) {
      localStorage.removeItem(STUDYFLOW_USERNAME_KEY);
    } else {
      localStorage.setItem(STUDYFLOW_USERNAME_KEY, identity.username.trim());
    }
  }

  deleteAccount(): Observable<void> {
    return this.http.delete<void>(`${this.apiBaseUrl}/auth/me`).pipe(
      tap(() => this.logout()),
      catchError((err) => {
        // If token is stale or account is already gone, still clear local session.
        if (err?.status === 401 || err?.status === 404) {
          this.logout();
        }
        return throwError(() => err);
      })
    );
  }

  private saveSession(r: SessionTokenResponse): void {
    localStorage.setItem(STUDYFLOW_TOKEN_KEY, r.access_token);
    localStorage.setItem(STUDYFLOW_USER_ID_KEY, String(r.user_id));
    localStorage.setItem(STUDYFLOW_EMAIL_KEY, r.email);
    if (r.username && r.username.trim()) {
      localStorage.setItem(STUDYFLOW_USERNAME_KEY, r.username.trim());
    } else {
      localStorage.removeItem(STUDYFLOW_USERNAME_KEY);
    }
    this.userIdSubject.next(r.user_id);
  }

  private readInitialUserId(): number | null {
    if (!localStorage.getItem(STUDYFLOW_TOKEN_KEY)) {
      localStorage.removeItem(STUDYFLOW_USER_ID_KEY);
      localStorage.removeItem(STUDYFLOW_EMAIL_KEY);
      localStorage.removeItem(STUDYFLOW_USERNAME_KEY);
      return null;
    }
    const raw = localStorage.getItem(STUDYFLOW_USER_ID_KEY);
    const n = raw ? parseInt(raw, 10) : NaN;
    return Number.isFinite(n) && n >= 1 ? n : null;
  }
}
