// cmp: pomodoro | tr: pomodoro tam sayfa — timer, başlat/durdur, konu / en: pomodoro full page timer start stop topic

import {
  Component,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  OnInit,
  Output,
  SimpleChanges,
} from "@angular/core";
import { Subscription } from "rxjs";

import { PomodoroSessionService, PomodoroTimerService, PomodoroTimerSnapshot } from "../../services/pomodoro";

@Component({
  selector: "app-pomodoro",
  templateUrl: "./pomodoro.component.html",
  styleUrls: ["./pomodoro.component.css"],
})
export class PomodoroComponent implements OnInit, OnDestroy, OnChanges {
  @Input() userId = 1;
  @Input() topic: string | null = null;

  @Output() pomodoroEnded = new EventEmitter<void>();

  snap: PomodoroTimerSnapshot | null = null;

  private subs = new Subscription();

  constructor(private pomoSession: PomodoroSessionService, private timer: PomodoroTimerService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes.userId && this.userId != null) {
      this.timer.setUserContext(this.userId, this.effectiveTopic());
    }
    if (changes.topic) {
      this.timer.setTopic(this.effectiveTopic());
    }
  }

  ngOnInit(): void {
    this.timer.setUserContext(this.userId, this.effectiveTopic());
    this.timer.setTopic(this.effectiveTopic());

    this.subs.add(
      this.pomoSession.topic$.subscribe((t) => {
        if (t) {
          this.topic = t;
          this.timer.setTopic(t);
        }
      })
    );
    this.subs.add(this.timer.state$.subscribe((s) => (this.snap = s)));
    this.subs.add(this.timer.focusSegmentEnded$.subscribe(() => this.pomodoroEnded.emit()));
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  private effectiveTopic(): string | null {
    if (this.topic == null) {
      return null;
    }
    const t = String(this.topic).trim();
    return t ? t : null;
  }

  formatTimer(): string {
    if (!this.snap) {
      return this.timer.formatMmSs(25 * 60);
    }
    return this.timer.formatMmSs(this.snap.remainingSeconds);
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

  startOrResume(): void {
    this.timer.startOrResume();
  }

  pause(): void {
    this.timer.pause();
  }

  reset(): void {
    this.timer.reset();
  }

  /** SVG circle metrics for countdown ring (viewBox 0 0 140 140). */
  readonly ring = { cx: 70, cy: 70, r: 56, sw: 10 } as const;

  get ringLen(): number {
    return 2 * Math.PI * this.ring.r;
  }

  /** 0 = phase done, 1 = full time remaining. */
  phaseProgressRatio(): number {
    if (!this.snap) {
      return 1;
    }
    const full = this.fullSecondsForSnap(this.snap);
    if (full <= 0) {
      return 0;
    }
    return Math.max(0, Math.min(1, this.snap.remainingSeconds / full));
  }

  ringDashoffset(): number {
    return this.ringLen * (1 - this.phaseProgressRatio());
  }

  cycleStepIndices(): number[] {
    return [0, 1, 2, 3, 4, 5, 6, 7];
  }

  cycleStepLabel(step: number): string {
    if (step === 7) {
      return "L";
    }
    if (step % 2 === 1) {
      return "S";
    }
    return String(step / 2 + 1);
  }

  cycleStepTitle(step: number): string {
    if (step === 7) {
      return "Long break";
    }
    if (step % 2 === 1) {
      return "Short break";
    }
    return `Focus ${step / 2 + 1} of 4`;
  }

  cycleStepClass(step: number): string {
    if (!this.snap) {
      return "sf-pomoSeg--up";
    }
    if (step < this.snap.stepIndex) {
      return "sf-pomoSeg--done";
    }
    if (step === this.snap.stepIndex) {
      return "sf-pomoSeg--current";
    }
    return "sf-pomoSeg--up";
  }

  /** Theme hook for ring, hero glow, and cycle strip “current” styling. */
  phaseThemeClass(): string {
    if (!this.snap) {
      return "";
    }
    return `sf-pomoPhase--${this.snap.phase}`;
  }
}
