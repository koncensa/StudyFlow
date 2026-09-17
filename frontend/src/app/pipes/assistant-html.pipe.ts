import { Pipe, PipeTransform } from "@angular/core";
import { DomSanitizer, SafeHtml } from "@angular/platform-browser";

/**
 * Renders assistant Markdown-like text safely: escaped HTML, **bold**, headings,
 * bullet/numbered lists (real &lt;ul&gt;/&lt;ol&gt;), then line breaks elsewhere.
 */
@Pipe({ name: "assistantHtml" })
export class AssistantHtmlPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {}

  transform(value: string | null | undefined): SafeHtml {
    if (value == null || value === "") {
      return this.sanitizer.bypassSecurityTrustHtml("");
    }
    let s = String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/^## (.+)$/gm, '<h3 class="pa-prose pa-prose-h3">$1</h3>');
    s = s.replace(/^### (.+)$/gm, '<h4 class="pa-prose pa-prose-h4">$1</h4>');
    s = s.replace(/^#### (.+)$/gm, '<h5 class="pa-prose pa-prose-h5">$1</h5>');
    s = this.listsAndLineBreaks(s);
    return this.sanitizer.bypassSecurityTrustHtml(s);
  }

  /** Turn `- item` / `* item` / `• item` and `1. item` runs into semantic lists; other newlines → &lt;br/&gt;. */
  private listsAndLineBreaks(s: string): string {
    const lines = s.split(/\n/);
    const parts: string[] = [];
    const plain: string[] = [];

    const flushPlain = (): void => {
      if (plain.length === 0) {
        return;
      }
      parts.push(plain.join("<br/>"));
      plain.length = 0;
    };

    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      // Allow `-item` as well as `- item` (models sometimes omit the space).
      const bullet = line.match(/^[\-*•]\s*(.+)$/);
      const numbered = line.match(/^\d+\.\s*(.+)$/);
      if (bullet) {
        flushPlain();
        const items: string[] = [];
        while (i < lines.length) {
          const m = lines[i].match(/^[\-*•]\s*(.+)$/);
          if (!m) {
            break;
          }
          items.push(`<li class="pa-prose-li">${this.decorateLeadLabel(m[1])}</li>`);
          i++;
        }
        parts.push(`<ul class="pa-prose-ul">${items.join("")}</ul>`);
        continue;
      }
      if (numbered) {
        flushPlain();
        const items: string[] = [];
        while (i < lines.length) {
          const m = lines[i].match(/^\d+\.\s*(.+)$/);
          if (!m) {
            break;
          }
          items.push(`<li class="pa-prose-li">${this.decorateLeadLabel(m[1])}</li>`);
          i++;
        }
        parts.push(`<ol class="pa-prose-ol">${items.join("")}</ol>`);
        continue;
      }
      plain.push(line);
      i++;
    }
    flushPlain();
    return parts.join("");
  }

  /**
   * Highlights list-item lead labels like "Linear Regression Scenario:" so
   * metadata stands out from the explanatory part of the sentence.
   */
  private decorateLeadLabel(text: string): string {
    const line = String(text || "").trim();
    if (!line || line.includes("://")) {
      return text;
    }
    const m = line.match(/^([^:\n]{3,90}:)\s+(.+)$/);
    if (!m) {
      return text;
    }
    const label = m[1].trim();
    const rest = m[2].trim();
    if (!rest) {
      return text;
    }
    return `<span class="pa-prose-leadLabel">${label}</span> ${rest}`;
  }
}
