import { Injectable, NgZone, OnDestroy } from "@angular/core";
import { BehaviorSubject, Subject } from "rxjs";
import { distinctUntilChanged } from "rxjs/operators";

import { CurrentUserService } from "../../session/current-user.service";
import {
  buildPomodoroTimerStorageKey,
  clearLegacyPomodoroTimerStorage,
} from "../../utils/pomodoro-timer-storage";
import { isValidStudyflowUserId } from "../../utils/session-user";
import { ApiService } from "../api.service";

/** 8-step cycle: F-S-F-S-F-S-F-L (indices 0..7) */
const CYCLE_LEN = 8;

export type PomodoroPhaseKind = "focus" | "short_break" | "long_break";

export interface PomodoroTimerSnapshot {
  remainingSeconds: number;
  isRunning: boolean;
  stepIndex: number;
  phase: PomodoroPhaseKind;
  phaseDisplay: string;
  nextPhaseDisplay: string;
  focusOrdinal: number;
  completedFocusTotal: number;
  effectiveFocusMin: number;
  effectiveShortMin: number;
  effectiveLongMin: number;
}

const DEFAULT_FOCUS_MIN = 25;
const DEFAULT_SHORT_MIN = 5;
const DEFAULT_LONG_MIN = 15;

interface InnerState {
  stepIndex: number;
  remainingSeconds: number;
  isRunning: boolean;
  phaseEndAt: number | null;
  backendSessionId: number | null;
  completedFocusTotal: number;
}

@Injectable({ providedIn: "root" })
export class PomodoroTimerService implements OnDestroy {
  readonly state$ = new BehaviorSubject<PomodoroTimerSnapshot>(this.emptySnapshot());
  /** Emitted when a focus block ends (timer finished or user paused during focus). */
  readonly focusSegmentEnded$ = new Subject<void>();

  private userId = 1;
  private activeStorageKey: string | null = null;
  private topic: string | null = null;
  private tickId: ReturnType<typeof setInterval> | null = null;
  private switchingUser = false;

  private inner: InnerState = {
    stepIndex: 0,
    remainingSeconds: DEFAULT_FOCUS_MIN * 60,
    isRunning: false,
    phaseEndAt: null,
    backendSessionId: null,
    completedFocusTotal: 0,
  };

  constructor(
    private api: ApiService,
    private ngZone: NgZone,
    private currentUser: CurrentUserService
  ) {
    clearLegacyPomodoroTimerStorage();
    this.pushState();
    this.currentUser.userId$.pipe(distinctUntilChanged()).subscribe((id) => {
      if (id == null) {
        void this.clearForLogout();
      }
    });
  }

  ngOnDestroy(): void {
    this.clearTicker();
  }

  setUserContext(userId: number, topic?: string | null): void {
    if (!isValidStudyflowUserId(userId)) {
      void this.clearForLogout();
      return;
    }
    const nextKey = buildPomodoroTimerStorageKey(userId, this.currentUser.email);
    if (nextKey == null) {
      return;
    }
    if (topic !== undefined) {
      this.topic = topic;
    }
    if (nextKey === this.activeStorageKey && userId === this.userId) {
      return;
    }
    void this.switchToUserScope(userId, nextKey);
  }

  setTopic(topic: string | null): void {
    this.topic = topic != null && String(topic).trim() ? String(topic).trim() : null;
  }

  startOrResume(): void {
    if (this.inner.isRunning) {
      return;
    }
    if (this.inner.remainingSeconds <= 0) {
      this.inner.remainingSeconds = this.durationSecForStep(this.inner.stepIndex);
    }
    this.inner.isRunning = true;
    this.inner.phaseEndAt = Date.now() + this.inner.remainingSeconds * 1000;
    if (this.isFocusStep(this.inner.stepIndex) && this.inner.backendSessionId == null) {
      void this.beginBackendFocusSession();
    }
    this.ensureTicker();
    this.persist();
    this.pushState();
  }

  pause(): void {
    if (!this.inner.isRunning) {
      return;
    }
    this.clearTicker();
    if (this.inner.phaseEndAt != null) {
      this.inner.remainingSeconds = Math.max(
        0,
        Math.floor((this.inner.phaseEndAt - Date.now()) / 1000)
      );
    }
    this.inner.phaseEndAt = null;
    this.inner.isRunning = false;
    this.persist();
    this.pushState();
  }

  reset(): void {
    this.clearTicker();
    void this.resetAsync();
  }

  /** Full timer reset: cancel any in-flight focus session, then restore cycle + UI counters. */
  private async resetAsync(): Promise<void> {
    await this.cancelForReset();
    this.inner.stepIndex = 0;
    this.inner.remainingSeconds = this.durationSecForStep(0);
    this.inner.isRunning = false;
    this.inner.phaseEndAt = null;
    this.inner.backendSessionId = null;
    this.inner.completedFocusTotal = 0;
    this.persist();
    this.pushState();
  }

  formatMmSs(totalSeconds: number): string {
    const t = Math.max(0, Math.floor(totalSeconds));
    const m = Math.floor(t / 60);
    const s = t % 60;
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }

  private emptySnapshot(): PomodoroTimerSnapshot {
    return {
      remainingSeconds: DEFAULT_FOCUS_MIN * 60,
      isRunning: false,
      stepIndex: 0,
      phase: "focus",
      phaseDisplay: "Focus",
      nextPhaseDisplay: "Short Break",
      focusOrdinal: 1,
      completedFocusTotal: 0,
      effectiveFocusMin: DEFAULT_FOCUS_MIN,
      effectiveShortMin: DEFAULT_SHORT_MIN,
      effectiveLongMin: DEFAULT_LONG_MIN,
    };
  }

  private phaseKind(step: number): PomodoroPhaseKind {
    if (step === 7) {
      return "long_break";
    }
    if (step % 2 === 1) {
      return "short_break";
    }
    return "focus";
  }

  private isFocusStep(step: number): boolean {
    return step >= 0 && step <= 6 && step % 2 === 0;
  }

  private effectiveFocusMin(): number {
    return DEFAULT_FOCUS_MIN;
  }

  private effectiveShortMin(): number {
    return DEFAULT_SHORT_MIN;
  }

  private effectiveLongMin(): number {
    return DEFAULT_LONG_MIN;
  }

  private durationSecForStep(step: number): number {
    const k = this.phaseKind(step);
    if (k === "long_break") {
      return this.effectiveLongMin() * 60;
    }
    if (k === "short_break") {
      return this.effectiveShortMin() * 60;
    }
    return this.effectiveFocusMin() * 60;
  }

  private phaseDisplay(step: number): string {
    switch (this.phaseKind(step)) {
      case "focus":
        return "Focus";
      case "short_break":
        return "Short Break";
      case "long_break":
        return "Long Break";
    }
  }

  private nextPhaseDisplayFrom(step: number): string {
    const next = (step + 1) % CYCLE_LEN;
    return this.phaseDisplay(next);
  }

  private focusOrdinalForStep(step: number): number {
    if (!this.isFocusStep(step)) {
      return 0;
    }
    return step / 2 + 1;
  }

  private buildSnapshot(): PomodoroTimerSnapshot {
    const step = this.inner.stepIndex;
    return {
      remainingSeconds: this.inner.remainingSeconds,
      isRunning: this.inner.isRunning,
      stepIndex: step,
      phase: this.phaseKind(step),
      phaseDisplay: this.phaseDisplay(step),
      nextPhaseDisplay: this.nextPhaseDisplayFrom(step),
      focusOrdinal: this.focusOrdinalForStep(step),
      completedFocusTotal: this.inner.completedFocusTotal,
      effectiveFocusMin: this.effectiveFocusMin(),
      effectiveShortMin: this.effectiveShortMin(),
      effectiveLongMin: this.effectiveLongMin(),
    };
  }

  private pushState(): void {
    this.state$.next(this.buildSnapshot());
  }

  private ensureTicker(): void {
    if (this.tickId != null) {
      return;
    }
    this.ngZone.runOutsideAngular(() => {
      this.tickId = setInterval(() => {
        this.ngZone.run(() => this.tick());
      }, 1000);
    });
  }

  private clearTicker(): void {
    if (this.tickId != null) {
      clearInterval(this.tickId);
      this.tickId = null;
    }
  }

  private tick(): void {
    if (!this.inner.isRunning || this.inner.phaseEndAt == null) {
      return;
    }
    const rem = Math.max(0, Math.floor((this.inner.phaseEndAt - Date.now()) / 1000));
    if (rem !== this.inner.remainingSeconds) {
      this.inner.remainingSeconds = rem;
      this.pushState();
    }
    if (rem <= 0) {
      void this.onPhaseTimeUp();
    }
  }

  private async onPhaseTimeUp(): Promise<void> {
    this.clearTicker();
    const wasFocus = this.isFocusStep(this.inner.stepIndex);
    if (wasFocus) {
      this.inner.completedFocusTotal += 1;
      this.focusSegmentEnded$.next();
      await this.endBackendFocusSession();
    }
    this.inner.stepIndex = (this.inner.stepIndex + 1) % CYCLE_LEN;
    const dur = this.durationSecForStep(this.inner.stepIndex);
    this.inner.remainingSeconds = dur;
    this.inner.isRunning = true;
    this.inner.phaseEndAt = Date.now() + dur * 1000;
    if (this.isFocusStep(this.inner.stepIndex)) {
      void this.beginBackendFocusSession();
    }
    this.persist();
    this.pushState();
    this.ensureTicker();
  }

  private async beginBackendFocusSession(): Promise<void> {
    const uid = this.userId;
    const stepWhenStarted = this.inner.stepIndex;
    if (!Number.isFinite(uid) || uid < 1 || !this.isFocusStep(stepWhenStarted)) {
      return;
    }
    try {
      const res = await this.api
        .pomodoroStart({ user_id: uid, topic: this.topic })
        .toPromise();
      if (
        this.inner.stepIndex !== stepWhenStarted ||
        !this.inner.isRunning ||
        !this.isFocusStep(this.inner.stepIndex)
      ) {
        await this.api.pomodoroCancel({ session_id: res.session_id }).toPromise().catch(() => {});
        return;
      }
      this.inner.backendSessionId = res.session_id;
      this.persist();
    } catch (e) {
      console.error("Pomodoro backend start failed", e);
    }
  }

  private async endBackendFocusSession(): Promise<void> {
    const id = this.inner.backendSessionId;
    if (id == null) {
      return;
    }
    this.inner.backendSessionId = null;
    this.persist();
    try {
      await this.api.pomodoroEnd({ session_id: id }).toPromise();
    } catch (e) {
      console.error("Pomodoro backend end failed", e);
    }
  }

  private async cancelBackendSessionById(id: number | null): Promise<boolean> {
    if (id == null) {
      return false;
    }
    try {
      await this.api.pomodoroCancel({ session_id: id }).toPromise();
      return true;
    } catch (e) {
      console.error("Pomodoro backend cancel failed", e);
      return false;
    }
  }

  private async cancelForReset(): Promise<void> {
    let changed = false;
    if (this.inner.backendSessionId != null) {
      const id = this.inner.backendSessionId;
      this.inner.backendSessionId = null;
      changed = await this.cancelBackendSessionById(id);
    }
    if (changed) {
      this.focusSegmentEnded$.next();
    }
  }

  private defaultInnerState(): InnerState {
    return {
      stepIndex: 0,
      remainingSeconds: DEFAULT_FOCUS_MIN * 60,
      isRunning: false,
      phaseEndAt: null,
      backendSessionId: null,
      completedFocusTotal: 0,
    };
  }

  private pauseInMemoryForScopeSwitch(): void {
    if (!this.inner.isRunning) {
      return;
    }
    this.clearTicker();
    if (this.inner.phaseEndAt != null) {
      this.inner.remainingSeconds = Math.max(
        0,
        Math.floor((this.inner.phaseEndAt - Date.now()) / 1000)
      );
    }
    this.inner.phaseEndAt = null;
    this.inner.isRunning = false;
  }

  private async switchToUserScope(userId: number, nextKey: string): Promise<void> {
    if (this.switchingUser) {
      return;
    }
    this.switchingUser = true;
    try {
      if (this.activeStorageKey) {
        this.pauseInMemoryForScopeSwitch();
        await this.cancelForReset();
        this.persist();
      }
      this.clearTicker();
      this.userId = userId;
      this.activeStorageKey = nextKey;
      this.inner = this.defaultInnerState();
      this.loadFromStorage(nextKey);
      this.reconcileAfterLoad();
      this.pushState();
      if (this.inner.isRunning) {
        this.ensureTicker();
      }
    } finally {
      this.switchingUser = false;
    }
  }

  private async clearForLogout(): Promise<void> {
    if (this.switchingUser) {
      return;
    }
    this.switchingUser = true;
    try {
      if (this.activeStorageKey) {
        this.pauseInMemoryForScopeSwitch();
        await this.cancelForReset();
        this.persist();
      }
      this.clearTicker();
      this.activeStorageKey = null;
      this.userId = 1;
      this.topic = null;
      this.inner = this.defaultInnerState();
      this.pushState();
    } finally {
      this.switchingUser = false;
    }
  }

  private persist(): void {
    if (!this.activeStorageKey) {
      return;
    }
    try {
      const payload = {
        v: 2,
        stepIndex: this.inner.stepIndex,
        remainingSeconds: this.inner.remainingSeconds,
        isRunning: this.inner.isRunning,
        phaseEndAt: this.inner.phaseEndAt,
        backendSessionId: this.inner.backendSessionId,
        completedFocusTotal: this.inner.completedFocusTotal,
      };
      localStorage.setItem(this.activeStorageKey, JSON.stringify(payload));
    } catch {
      /* ignore quota / private mode */
    }
  }

  private loadFromStorage(storageKey: string): void {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) {
        return;
      }
      const p = JSON.parse(raw);
      if (p.v !== 2) {
        return;
      }
      this.inner.stepIndex = Math.min(Math.max(0, Math.floor(p.stepIndex)), CYCLE_LEN - 1);
      this.inner.completedFocusTotal = Math.max(0, Math.floor(p.completedFocusTotal || 0));
      this.inner.backendSessionId =
        p.backendSessionId == null ? null : Math.floor(p.backendSessionId);
      this.inner.isRunning = !!p.isRunning;
      const rawEnd = p.phaseEndAt == null ? null : Math.floor(Number(p.phaseEndAt));
      this.inner.phaseEndAt =
        rawEnd != null && Number.isFinite(rawEnd) ? rawEnd : null;
      if (this.inner.isRunning && this.inner.phaseEndAt != null) {
        this.inner.remainingSeconds = Math.max(
          0,
          Math.floor((this.inner.phaseEndAt - Date.now()) / 1000)
        );
      } else {
        const rs = Math.floor(Number(p.remainingSeconds));
        const maxDur = this.durationSecForStep(this.inner.stepIndex);
        this.inner.remainingSeconds = Math.min(Math.max(0, rs), maxDur);
        this.inner.isRunning = false;
        this.inner.phaseEndAt = null;
      }
    } catch {
      /* ignore */
    }
  }

  /**
   * After reload: if the deadline is in the past, advance exactly one phase and pause.
   * Avoids chaining many "virtual" completions while the tab was closed.
   */
  private reconcileAfterLoad(): void {
    if (!this.inner.isRunning || this.inner.phaseEndAt == null) {
      return;
    }
    if (Date.now() < this.inner.phaseEndAt) {
      this.inner.remainingSeconds = Math.max(
        0,
        Math.floor((this.inner.phaseEndAt - Date.now()) / 1000)
      );
      return;
    }
    const wasFocus = this.isFocusStep(this.inner.stepIndex);
    if (wasFocus) {
      this.inner.completedFocusTotal += 1;
      void this.endBackendFocusSession();
    }
    this.inner.stepIndex = (this.inner.stepIndex + 1) % CYCLE_LEN;
    this.inner.remainingSeconds = this.durationSecForStep(this.inner.stepIndex);
    this.inner.isRunning = false;
    this.inner.phaseEndAt = null;
    this.inner.backendSessionId = null;
    this.persist();
  }
}
