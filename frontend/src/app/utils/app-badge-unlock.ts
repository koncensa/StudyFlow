import { ProfileBadgeItem, ProfileResponse } from "../models/types";

export function resolveNewBadgeUnlocks(params: {
  previous: ProfileResponse | null;
  current: ProfileResponse;
  hydrated: boolean;
}): {
  nextHydrated: boolean;
  newBadges: ProfileBadgeItem[];
} {
  const earnedNow = (params.current.badges || []).filter((b) => b.earned);
  if (!params.hydrated) {
    return {
      nextHydrated: true,
      newBadges: [],
    };
  }

  const prevEarned = new Set(
    (params.previous?.badges || [])
      .filter((b) => b.earned)
      .map((b) => String(b.slug || "").trim().toLowerCase())
      .filter(Boolean)
  );
  const newBadges = earnedNow.filter((b) => {
    const slug = String(b.slug || "").trim().toLowerCase();
    return !!slug && !prevEarned.has(slug);
  });

  return {
    nextHydrated: true,
    newBadges,
  };
}
