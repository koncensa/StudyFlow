import { Component, EventEmitter, Output } from "@angular/core";

import { CurrentUserService } from "./current-user.service";
import { I18nService } from "../i18n/i18n.service";

@Component({
  selector: "app-session-header",
  templateUrl: "./session-header.component.html",
  styleUrls: ["./session-header.component.css"],
})
export class SessionHeaderComponent {
  @Output() openSession = new EventEmitter<"login" | "register">();

  constructor(
    public user: CurrentUserService,
    public i18n: I18nService
  ) {}

  clickLogin(): void {
    this.openSession.emit("login");
  }

  clickSignUp(): void {
    this.openSession.emit("register");
  }

  clickLogout(): void {
    this.user.logout();
  }
}
