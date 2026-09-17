import {
  Component,
  EventEmitter,
  HostListener,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
} from "@angular/core";

import { I18nService } from "../i18n/i18n.service";
import { CurrentUserService } from "./current-user.service";

export type SessionDialogTab = "login" | "register";

@Component({
  selector: "app-session-dialog",
  templateUrl: "./session-dialog.component.html",
  styleUrls: ["./session-dialog.component.css"],
})
export class SessionDialogComponent implements OnChanges, OnDestroy {
  @Input() visible = false;
  @Input() initialTab: SessionDialogTab = "login";
  /** Shown above the form when opened from a protected feature (e.g. route guard). */
  @Input() noticeMessage: string | null = null;

  @Output() visibleChange = new EventEmitter<boolean>();
  @Output() authenticated = new EventEmitter<void>();

  tab: SessionDialogTab = "login";

  fullName = "";
  email = "";
  password = "";

  busy = false;

  emailError = "";
  passwordError = "";
  formError = "";
  /** After successful sign-up, before user logs in */
  successBanner = "";

  constructor(private currentUser: CurrentUserService, private i18n: I18nService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes.visible) {
      document.body.style.overflow = this.visible ? "hidden" : "";
      if (this.visible) {
        this.tab = this.initialTab;
        this.fullName = "";
        this.email = "";
        this.password = "";
        this.clearErrors();
        this.busy = false;
        this.successBanner = "";
      }
    } else if (this.visible && changes.initialTab) {
      this.tab = this.initialTab;
      this.clearErrors();
    }
  }

  ngOnDestroy(): void {
    document.body.style.overflow = "";
  }

  @HostListener("document:keydown.escape")
  onEscape(): void {
    if (this.visible) {
      this.close();
    }
  }

  close(): void {
    this.visible = false;
    this.visibleChange.emit(false);
    document.body.style.overflow = "";
  }

  setTab(t: SessionDialogTab): void {
    if (this.tab === t) {
      return;
    }
    this.tab = t;
    this.clearErrors();
    this.successBanner = "";
  }

  private clearErrors(): void {
    this.emailError = "";
    this.passwordError = "";
    this.formError = "";
  }

  private validateClient(): boolean {
    this.clearErrors();
    let ok = true;
    const email = this.email.trim();
    if (!email) {
      this.emailError = this.i18n.t("errors.emailRequired");
      ok = false;
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      this.emailError = this.i18n.t("errors.emailInvalid");
      ok = false;
    }
    if (!this.password) {
      this.passwordError = this.i18n.t("errors.passwordRequired");
      ok = false;
    } else if (this.tab === "register" && this.password.length < 8) {
      this.passwordError = this.i18n.t("errors.passwordShort");
      ok = false;
    }
    return ok;
  }

  private formatServerError(err: any): string {
    const d = err?.error?.detail;
    if (typeof d === "string") {
      return d;
    }
    if (Array.isArray(d)) {
      const parts = d.map((x: any) => (typeof x?.msg === "string" ? x.msg : JSON.stringify(x)));
      return parts.filter(Boolean).join(" ");
    }
    return this.i18n.t("errors.generic");
  }

  submit(): void {
    if (!this.validateClient()) {
      return;
    }
    this.formError = "";
    this.successBanner = "";
    this.busy = true;
    const email = this.email.trim();
    const nameTrim = this.fullName.trim();

    if (this.tab === "login") {
      this.currentUser.login(email, this.password).subscribe({
        next: () => this.onLoginSuccess(),
        error: (err) => this.onFail(err),
      });
    } else {
      this.currentUser.register(email, this.password, nameTrim || undefined).subscribe({
        next: () => this.onRegisterSuccess(),
        error: (err) => this.onFail(err),
      });
    }
  }

  private onRegisterSuccess(): void {
    this.busy = false;
    this.password = "";
    this.formError = "";
    this.successBanner = this.i18n.t("sessionDlg.registerOk");
    this.tab = "login";
  }

  private onLoginSuccess(): void {
    this.busy = false;
    this.password = "";
    this.successBanner = "";
    this.authenticated.emit();
    this.close();
  }

  private onFail(err: any): void {
    this.busy = false;
    this.formError = this.formatServerError(err);
  }
}
