import { Injectable } from "@angular/core";
import { BehaviorSubject } from "rxjs";

/**
 * Floating pomodoro: topic + panel visibility survive route changes.
 * Timer API calls stay in PomodoroWidgetComponent.
 */
@Injectable({ providedIn: "root" })
export class PomodoroSessionService {
  readonly topic$ = new BehaviorSubject<string | null>(null);
  readonly expanded$ = new BehaviorSubject<boolean>(true);

  setTopic(topic: string | null): void {
    const t = topic != null && String(topic).trim() ? String(topic).trim() : null;
    this.topic$.next(t);
  }

  expand(): void {
    this.expanded$.next(true);
  }

  collapse(): void {
    this.expanded$.next(false);
  }

  toggleExpanded(): void {
    this.expanded$.next(!this.expanded$.value);
  }
}
