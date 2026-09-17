// cmp: pomodoro-widget | tr: yüzen pomodoro widget — her ekranda küçük timer / en: floating pomodoro widget mini timer on all screens

import {
  Component,
  Input,
  OnChanges,
  OnDestroy,
  OnInit,
  SimpleChanges,
} from "@angular/core";
import { Subscription } from "rxjs";

import { PomodoroSessionService, PomodoroTimerService, PomodoroTimerSnapshot } from "../../services/pomodoro";

@Component({
  selector: "app-pomodoro-widget",
  templateUrl: "./pomodoro-widget.component.html",
  styleUrls: ["./pomodoro-widget.component.css"],
})
export class PomodoroWidgetComponent implements OnInit, OnDestroy, OnChanges {
  @Input() userId = 1;

  expanded = true;
  topic: string | null = null;
  snap: PomodoroTimerSnapshot | null = null;

  private subs = new Subscription();

  constructor(private timer: PomodoroTimerService, private session: PomodoroSessionService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes.userId && this.userId != null) {
      this.timer.setUserContext(this.userId, this.topic);
    }
  }

  ngOnInit(): void {
    this.timer.setUserContext(this.userId, this.topic);
    this.subs.add(
      this.session.topic$.subscribe((t) => {
        this.topic = t;
        this.timer.setTopic(t);
      })
    );
    this.subs.add(
      this.session.expanded$.subscribe((e) => {
        this.expanded = e;
      })
    );
    this.subs.add(
      this.timer.state$.subscribe((s) => {
        this.snap = s;
      })
    );
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  formatTimer(): string {
    if (!this.snap) {
      return this.timer.formatMmSs(25 * 60);
    }
    return this.timer.formatMmSs(this.snap.remainingSeconds);
  }

  togglePanel(): void {
    this.session.toggleExpanded();
  }

  startOrResume(): void {
    this.timer.startOrResume();
  }

  pause(): void {
    this.timer.pause();
  }

  reset(): void {
    this.timer.reset();
  }

  primaryActionLabel(): string {
    if (!this.snap || this.snap.isRunning) {
      return "Start";
    }
    const full = this.fullSecondsForSnap(this.snap);
    return this.snap.remainingSeconds < full ? "Resume" : "Start";
  }

  private fullSecondsForSnap(s: PomodoroTimerSnapshot): number {
    if (s.phase === "long_break") {
      return s.effectiveLongMin * 60;
    }
    if (s.phase === "short_break") {
      return s.effectiveShortMin * 60;
    }
    return s.effectiveFocusMin * 60;
  }
}
