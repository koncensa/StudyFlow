// cmp: user-profile | tr: kullanıcı profili — hesap, foto, şifre, rozetler / en: user profile account photo password badges

import { Component, ElementRef, EventEmitter, OnInit, Output, ViewChild } from "@angular/core";

import { environment } from "../../../environments/environment";
import { UserMeResponse } from "../../models/types";
import { ApiService } from "../../services/api.service";
import { CurrentUserService } from "../../session/current-user.service";
import { I18nService } from "../../i18n/i18n.service";
import { readApiError } from "../../utils/api-error";
import { isUnauthorizedHttpError } from "../../utils/app-profile";

@Component({
  selector: "app-user-profile",
  templateUrl: "./user-profile.component.html",
  styleUrls: ["./user-profile.component.css"],
})
export class UserProfileComponent implements OnInit {
  private static readonly MIN_PHOTO_SIDE_PX = 600;
  private static readonly PROFILE_DRAFTS_KEY_PREFIX = "studyflow:profile-drafts:";
  private static readonly PROFILE_PHOTO_DRAFT_KEY_PREFIX = "studyflow:profile-photo-draft:";
  @Output() afterAccountDelete = new EventEmitter<void>();
  @ViewChild("photoInput") photoInputRef?: ElementRef<HTMLInputElement>;

  account: UserMeResponse | null = null;
  loadError: string | null = null;

  usernameDraft = "";
  biographyDraft = "";
  currentPassword = "";
  newPassword = "";
  confirmPassword = "";

  profileError: string | null = null;
  passwordError: string | null = null;
  passwordNotice: string | null = null;
  photoError: string | null = null;
  photoNotice: string | null = null;

  selectedFile: File | null = null;
  /** Data URL for preview */
  photoPreview: string | null = null;
  avatarPosX = 0;
  avatarPosY = 0;
  avatarZoom = 1;
  draftAvatarPosX = 0;
  draftAvatarPosY = 0;
  draftAvatarZoom = 1;
  showPositionEditor = false;

  showPasswordPanel = false;
  showDeleteModal = false;
  deleteBusy = false;
  deleteError: string | null = null;
  photoUploadBusy = false;
  saveNicknameBusy = false;
  saveBiographyBusy = false;
  passwordSaveBusy = false;

  readonly apiBase = environment.apiBaseUrl;

  constructor(
    public api: ApiService,
    public currentUser: CurrentUserService,
    public i18n: I18nService
  ) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loadError = null;
    this.api.getMe().subscribe({
      next: (m) => {
        this.account = m;
        this.usernameDraft = m.username || "";
        this.biographyDraft = m.biography || "";
        this.restoreDraftsIfAny();
        this.currentUser.updateSessionIdentity({ email: m.email, username: m.username });
        this.loadAvatarPosition();
        this.clearPhotoSelection(false);
        this.restorePhotoDraftIfAny();
      },
      error: (e: unknown) => {
        this.loadError = readApiError(e, this.i18n.t("profile.loadError"));
        if (isUnauthorizedHttpError(e)) {
          this.currentUser.logout();
        }
      },
    });
  }

  displayAvatarUrl(): string | null {
    if (this.photoPreview) {
      return this.photoPreview;
    }
    if (!this.account?.profile_photo_url) {
      return null;
    }
    const p = this.account.profile_photo_url;
    if (p.startsWith("http://") || p.startsWith("https://")) {
      return p;
    }
    const path = p.startsWith("/") ? p : `/${p}`;
    return `${this.apiBase}${path}`;
  }

  avatarImageTransform(): string {
    const x = this.showPositionEditor ? this.draftAvatarPosX : this.avatarPosX;
    const y = this.showPositionEditor ? this.draftAvatarPosY : this.avatarPosY;
    const z = this.showPositionEditor ? this.draftAvatarZoom : this.avatarZoom;
    return `translate(${x.toFixed(2)}%, ${y.toFixed(2)}%) scale(${z.toFixed(3)})`;
  }

  private avatarPositionStorageKey(): string | null {
    const uid = this.account?.user_id;
    return uid ? `studyflow:avatar-position:${uid}` : null;
  }

  private loadAvatarPosition(): void {
    this.avatarPosX = 0;
    this.avatarPosY = 0;
    this.avatarZoom = 1;
    const key = this.avatarPositionStorageKey();
    if (!key) return;
    const raw = localStorage.getItem(key);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as { x?: number; y?: number; z?: number };
      if (Number.isFinite(parsed?.x)) this.avatarPosX = Math.max(-35, Math.min(35, Number(parsed.x)));
      if (Number.isFinite(parsed?.y)) this.avatarPosY = Math.max(-35, Math.min(35, Number(parsed.y)));
      if (Number.isFinite(parsed?.z)) this.avatarZoom = Math.max(1, Math.min(2, Number(parsed.z)));
    } catch {
      // ignore invalid saved preference
    }
    this.draftAvatarPosX = this.avatarPosX;
    this.draftAvatarPosY = this.avatarPosY;
    this.draftAvatarZoom = this.avatarZoom;
  }

  private persistAvatarPosition(): void {
    const key = this.avatarPositionStorageKey();
    if (!key) return;
    localStorage.setItem(key, JSON.stringify({ x: this.avatarPosX, y: this.avatarPosY, z: this.avatarZoom }));
  }

  onAvatarPosXChange(v: number): void {
    this.draftAvatarPosX = Math.max(-35, Math.min(35, Number(v) || 0));
  }

  onAvatarPosYChange(v: number): void {
    this.draftAvatarPosY = Math.max(-35, Math.min(35, Number(v) || 0));
  }

  onAvatarZoomChange(v: number): void {
    this.draftAvatarZoom = Math.max(1, Math.min(2, Number(v) || 1));
  }

  resetAvatarPosition(): void {
    this.draftAvatarPosX = 0;
    this.draftAvatarPosY = 0;
    this.draftAvatarZoom = 1;
  }

  saveAvatarPosition(): void {
    this.avatarPosX = this.draftAvatarPosX;
    this.avatarPosY = this.draftAvatarPosY;
    this.avatarZoom = this.draftAvatarZoom;
    this.persistAvatarPosition();
    this.showPositionEditor = false;
  }

  cancelAvatarPositionEdit(): void {
    this.draftAvatarPosX = this.avatarPosX;
    this.draftAvatarPosY = this.avatarPosY;
    this.draftAvatarZoom = this.avatarZoom;
    this.showPositionEditor = false;
  }

  private resetAvatarPositionApplied(): void {
    this.avatarPosX = 0;
    this.avatarPosY = 0;
    this.avatarZoom = 1;
    this.draftAvatarPosX = 0;
    this.draftAvatarPosY = 0;
    this.draftAvatarZoom = 1;
    this.persistAvatarPosition();
  }

  defaultAvatarLetter(): string {
    const n = (this.usernameDraft || "").trim();
    if (n) {
      return n[0].toUpperCase();
    }
    const e = this.account?.email || "";
    return (e[0] || "?").toUpperCase();
  }

  onFileChange(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0] ?? null;
    void this.applySelectedFile(f, input);
  }

  onNicknameDraftChange(v: string): void {
    this.usernameDraft = v ?? "";
    this.persistDrafts();
  }

  onBiographyDraftChange(v: string): void {
    this.biographyDraft = v ?? "";
    this.persistDrafts();
  }

  private photoDraftStorageKey(): string | null {
    const uid = this.account?.user_id;
    return uid ? `${UserProfileComponent.PROFILE_PHOTO_DRAFT_KEY_PREFIX}${uid}` : null;
  }

  private persistPhotoDraft(file: File, dataUrl: string): void {
    const key = this.photoDraftStorageKey();
    if (!key) return;
    localStorage.setItem(
      key,
      JSON.stringify({
        name: file.name,
        type: file.type || "image/jpeg",
        dataUrl,
      })
    );
  }

  private clearPersistedPhotoDraft(): void {
    const key = this.photoDraftStorageKey();
    if (!key) return;
    localStorage.removeItem(key);
  }

  private restorePhotoDraftIfAny(): void {
    const key = this.photoDraftStorageKey();
    if (!key) return;
    const raw = localStorage.getItem(key);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as { name?: string; type?: string; dataUrl?: string };
      if (!parsed?.dataUrl || typeof parsed.dataUrl !== "string") return;
      const file = this.dataUrlToFile(parsed.dataUrl, parsed.name || "photo-draft.jpg", parsed.type || "image/jpeg");
      this.selectedFile = file;
      this.photoPreview = parsed.dataUrl;
    } catch {
      // ignore malformed draft
    }
  }

  private dataUrlToFile(dataUrl: string, name: string, type: string): File {
    const parts = dataUrl.split(",");
    const b64 = parts.length > 1 ? parts[1] : "";
    const binary = atob(b64);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return new File([bytes], name, { type });
  }

  private profileDraftsStorageKey(): string | null {
    const uid = this.account?.user_id;
    return uid ? `${UserProfileComponent.PROFILE_DRAFTS_KEY_PREFIX}${uid}` : null;
  }

  private persistDrafts(): void {
    const key = this.profileDraftsStorageKey();
    if (!key) return;
    localStorage.setItem(
      key,
      JSON.stringify({
        usernameDraft: this.usernameDraft,
        biographyDraft: this.biographyDraft,
      })
    );
  }

  private restoreDraftsIfAny(): void {
    const key = this.profileDraftsStorageKey();
    if (!key) return;
    const raw = localStorage.getItem(key);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as { usernameDraft?: string; biographyDraft?: string };
      if (typeof parsed.usernameDraft === "string") {
        this.usernameDraft = parsed.usernameDraft;
      }
      if (typeof parsed.biographyDraft === "string") {
        this.biographyDraft = parsed.biographyDraft;
      }
    } catch {
      // ignore malformed drafts
    }
  }

  private async applySelectedFile(f: File | null, input?: HTMLInputElement | null): Promise<void> {
    this.photoError = null;
    this.photoNotice = null;
    this.selectedFile = f;
    this.photoPreview = null;
    if (!f) {
      return;
    }
    if (f.size > 2 * 1024 * 1024) {
      this.photoError = this.i18n.lang === "tr" ? "En fazla 2 MB." : "Max 2 MB.";
      this.clearFileInput(input || null);
      this.selectedFile = null;
      return;
    }
    if (!/\.(jpe?g|png)$/i.test(f.name)) {
      this.photoError = this.i18n.lang === "tr" ? "Sadece JPG veya PNG." : "JPG or PNG only.";
      this.clearFileInput(input || null);
      this.selectedFile = null;
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      this.photoPreview = typeof reader.result === "string" ? reader.result : null;
      if (!this.photoPreview) {
        return;
      }
      const img = new Image();
      img.onload = () => {
        const minSide = Math.min(img.naturalWidth || 0, img.naturalHeight || 0);
        if (minSide < UserProfileComponent.MIN_PHOTO_SIDE_PX) {
          this.photoError =
            this.i18n.lang === "tr"
              ? `Fotoğraf en az ${UserProfileComponent.MIN_PHOTO_SIDE_PX}x${UserProfileComponent.MIN_PHOTO_SIDE_PX}px olmalı.`
              : `Image must be at least ${UserProfileComponent.MIN_PHOTO_SIDE_PX}x${UserProfileComponent.MIN_PHOTO_SIDE_PX}px.`;
          this.clearFileInput(input || null);
          this.clearPhotoSelection();
          return;
        }
        this.persistPhotoDraft(f, this.photoPreview!);
      };
      img.onerror = () => {
        this.photoError = this.i18n.lang === "tr" ? "Görsel okunamadı." : "Could not read image.";
        this.clearFileInput(input || null);
        this.clearPhotoSelection();
      };
      img.src = this.photoPreview;
    };
    reader.readAsDataURL(f);
  }

  onEditPhotoClick(): void {
    if (this.photoUploadBusy) {
      return;
    }
    if (!this.displayAvatarUrl()) {
      this.photoError = this.i18n.lang === "tr" ? "Önce profil fotoğrafı yükleyin." : "Upload a profile photo first.";
      return;
    }
    if (this.showPositionEditor) {
      this.cancelAvatarPositionEdit();
      return;
    }
    this.draftAvatarPosX = this.avatarPosX;
    this.draftAvatarPosY = this.avatarPosY;
    this.draftAvatarZoom = this.avatarZoom;
    this.showPositionEditor = true;
  }

  private clearFileInput(input: HTMLInputElement | null): void {
    if (input) {
      input.value = "";
    }
  }

  clearPhotoSelection(clearPersistedDraft = true): void {
    this.selectedFile = null;
    this.photoPreview = null;
    this.photoError = null;
    this.showPositionEditor = false;
    this.clearFileInput(this.photoInputRef?.nativeElement ?? null);
    if (clearPersistedDraft) {
      this.clearPersistedPhotoDraft();
    }
  }

  get selectedFileName(): string {
    return this.selectedFile?.name || "";
  }

  canRemovePhoto(): boolean {
    return !!(this.account?.profile_photo_url || this.selectedFile || this.photoPreview);
  }

  removePhoto(): void {
    if (this.photoUploadBusy) {
      return;
    }
    // If user only selected a new file (not uploaded yet), allow quick reset to default.
    if (!this.account?.profile_photo_url) {
      this.clearPhotoSelection();
      return;
    }
    this.photoUploadBusy = true;
    this.photoError = null;
    this.photoNotice = null;
    this.api.deleteProfilePhoto().subscribe({
      next: (m) => {
        this.account = m;
        this.clearPhotoSelection();
        this.photoUploadBusy = false;
        this.photoNotice = this.i18n.lang === "tr" ? "Profil fotoğrafı kaldırıldı." : "Profile photo removed.";
      },
      error: (e: unknown) => {
        this.photoUploadBusy = false;
        this.photoError = readApiError(
          e,
          this.i18n.lang === "tr" ? "Fotoğraf kaldırılamadı." : "Could not remove photo."
        );
        if (isUnauthorizedHttpError(e)) {
          this.currentUser.logout();
        }
      },
    });
  }

  async uploadPhoto(): Promise<void> {
    if (!this.selectedFile) {
      return;
    }
    this.photoUploadBusy = true;
    this.photoError = null;
    this.photoNotice = null;
    this.api.uploadProfilePhoto(this.selectedFile).subscribe({
      next: (m) => {
        this.account = m;
        this.resetAvatarPositionApplied();
        this.clearPhotoSelection();
        this.photoUploadBusy = false;
        this.photoNotice = this.i18n.lang === "tr" ? "Profil fotoğrafı güncellendi." : "Profile photo updated.";
      },
      error: (e: unknown) => {
        this.photoUploadBusy = false;
        this.photoError = readApiError(
          e,
          this.i18n.lang === "tr" ? "Yükleme başarısız." : "Upload failed."
        );
        if (isUnauthorizedHttpError(e)) {
          this.currentUser.logout();
        }
      },
    });
  }

  onUpdatePhotoClick(): void {
    if (this.photoUploadBusy) {
      return;
    }
    if (!this.selectedFile) {
      this.photoInputRef?.nativeElement.click();
      return;
    }
    void this.uploadPhoto();
  }

  saveNickname(): void {
    this.profileError = null;
    this.saveNicknameBusy = true;
    const uname = this.usernameDraft.trim() || null;
    const biography = this.account?.biography ?? null;
    this.api.patchMe({ username: uname, biography }).subscribe({
      next: (m) => {
        this.account = m;
        this.usernameDraft = m.username || "";
        this.biographyDraft = m.biography || "";
        this.persistDrafts();
        this.currentUser.updateSessionIdentity({ email: m.email, username: m.username });
        this.saveNicknameBusy = false;
      },
      error: (e: unknown) => {
        this.saveNicknameBusy = false;
        this.profileError = readApiError(
          e,
          this.i18n.lang === "tr" ? "Kaydedilemedi. Tekrar dene." : "Could not save. Try again."
        );
        if (isUnauthorizedHttpError(e)) {
          this.currentUser.logout();
        }
      },
    });
  }

  saveBiography(): void {
    this.profileError = null;
    this.saveBiographyBusy = true;
    const biography = this.biographyDraft.trim() || null;
    const uname = this.account?.username ?? null;
    this.api.patchMe({ username: uname, biography }).subscribe({
      next: (m) => {
        this.account = m;
        this.usernameDraft = m.username || "";
        this.biographyDraft = m.biography || "";
        this.persistDrafts();
        this.currentUser.updateSessionIdentity({ email: m.email, username: m.username });
        this.saveBiographyBusy = false;
      },
      error: (e: unknown) => {
        this.saveBiographyBusy = false;
        this.profileError = readApiError(
          e,
          this.i18n.lang === "tr" ? "Kaydedilemedi. Tekrar dene." : "Could not save. Try again."
        );
        if (isUnauthorizedHttpError(e)) {
          this.currentUser.logout();
        }
      },
    });
  }

  togglePasswordPanel(): void {
    this.showPasswordPanel = !this.showPasswordPanel;
    this.passwordError = null;
    this.passwordNotice = null;
    if (!this.showPasswordPanel) {
      this.currentPassword = "";
      this.newPassword = "";
      this.confirmPassword = "";
    }
  }

  savePassword(): void {
    this.passwordError = null;
    this.passwordNotice = null;
    if (this.newPassword.length < 8) {
      this.passwordError = this.i18n.t("errors.passwordShort");
      return;
    }
    if (this.newPassword !== this.confirmPassword) {
      this.passwordError = this.i18n.lang === "tr" ? "Yeni şifreler eşleşmiyor." : "New passwords do not match.";
      return;
    }
    this.passwordSaveBusy = true;
    this.api
      .changePassword({
        current_password: this.currentPassword,
        new_password: this.newPassword,
        confirm_new_password: this.confirmPassword,
      })
      .subscribe({
        next: (m) => {
          this.account = m;
          this.passwordSaveBusy = false;
          this.currentPassword = "";
          this.newPassword = "";
          this.confirmPassword = "";
          this.showPasswordPanel = false;
          this.passwordNotice = this.i18n.lang === "tr" ? "Şifreniz değiştirildi." : "Your password has been changed.";
        },
        error: (e: unknown) => {
          this.passwordSaveBusy = false;
          this.passwordError = readApiError(
            e,
            this.i18n.lang === "tr" ? "Şifre güncellenemedi. Mevcut şifreyi kontrol et." : "Could not update password. Check your current password."
          );
          if (isUnauthorizedHttpError(e)) {
            this.currentUser.logout();
          }
        },
      });
  }

  openDeleteModal(): void {
    this.deleteError = null;
    this.showDeleteModal = true;
  }

  closeDeleteModal(): void {
    if (this.deleteBusy) {
      return;
    }
    this.showDeleteModal = false;
  }

  confirmDeleteAccount(): void {
    this.deleteBusy = true;
    this.deleteError = null;
    this.currentUser.deleteAccount().subscribe({
      next: () => {
        this.deleteBusy = false;
        this.showDeleteModal = false;
        this.afterAccountDelete.emit();
      },
      error: (e: unknown) => {
        this.deleteBusy = false;
        this.deleteError = readApiError(
          e,
          this.i18n.lang === "tr" ? "Hesap silinemedi. Tekrar dene." : "Could not delete account. Try again."
        );
        if (isUnauthorizedHttpError(e)) {
          this.closeDeleteModal();
        }
      },
    });
  }
}
