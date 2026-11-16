// =============================================================================
// PH Agent Hub — Optional completion sound (Issue #619)
// =============================================================================
// A short synthesized chime instead of a bundled asset: no network request,
// no extra file to cache, and the volume is under the app's control.  The
// setting is off by default, and any failure is swallowed — a browser that
// blocks audio must never break a notification.
// =============================================================================

let audioContext: AudioContext | null = null;

function contextClass(): typeof AudioContext | null {
  if (typeof window === "undefined") return null;
  const maybe = window as unknown as {
    AudioContext?: typeof AudioContext;
    webkitAudioContext?: typeof AudioContext;
  };
  return maybe.AudioContext ?? maybe.webkitAudioContext ?? null;
}

export function playCompletionSound(): void {
  try {
    const Ctx = contextClass();
    if (!Ctx) return;
    audioContext ??= new Ctx();
    const ctx = audioContext;
    if (ctx.state === "suspended") void ctx.resume();

    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.08, ctx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
    oscillator.connect(gain).connect(ctx.destination);
    oscillator.start();
    oscillator.stop(ctx.currentTime + 0.25);
  } catch {
    // Audio is optional.
  }
}
