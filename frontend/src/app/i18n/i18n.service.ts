import { Injectable } from "@angular/core";

import { AppLang, normalizeAppLang } from "./app-lang";
import { UI_STRINGS } from "./ui-strings";

const STORAGE_KEY = "studyflow_ui_lang";

@Injectable({ providedIn: "root" })
export class I18nService {
  private active: AppLang = "en";

  constructor() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        this.active = normalizeAppLang(saved);
      }
    } catch {
      /* private mode / quota */
    }
  }

  get lang(): AppLang {
    return this.active;
  }

  /** Switch UI strings (and optionally sync with profile.locale on the server later). */
  use(lang: AppLang): void {
    this.active = lang;
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* ignore */
    }
  }

  t(key: string): string {
    const pack = UI_STRINGS[this.active] || UI_STRINGS.en;
    return pack[key] ?? UI_STRINGS.en[key] ?? key;
  }
}
