import { Pipe, PipeTransform } from "@angular/core";

import { I18nService } from "./i18n.service";

/** Looks up static UI copy by key; `pure: false` so templates refresh after `I18nService.use()`. */
@Pipe({ name: "lang", pure: false })
export class LangPipe implements PipeTransform {
  constructor(private i18n: I18nService) {}

  transform(key: string): string {
    return this.i18n.t(key);
  }
}
