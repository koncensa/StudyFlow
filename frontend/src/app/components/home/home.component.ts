// cmp: home | tr: ana sayfa — hero, menü, misafir uyarısı / en: home page hero menu guest notice

import { Component, EventEmitter, Input, Output } from "@angular/core";

import { AppLang } from "../../i18n/app-lang";
import { ProfileBadgeItem } from "../../models/types";

export type HomeNavTarget = "pdf" | "results" | "pomo" | "plan" | "analysis";

@Component({
  selector: "app-home",
  templateUrl: "./home.component.html",
  styleUrls: ["./home.component.css"],
})
export class HomeComponent {
  @Input() isAuthenticated = false;
  @Input() locale: AppLang = "en";
  @Input() starterBadges: ProfileBadgeItem[] = [];

  @Output() startFlow = new EventEmitter<void>();
  @Output() goQuizFromHero = new EventEmitter<void>();
  @Output() openQuiz = new EventEmitter<void>();
  @Output() openSession = new EventEmitter<"login" | "register">();
  @Output() navigate = new EventEmitter<HomeNavTarget>();
}
