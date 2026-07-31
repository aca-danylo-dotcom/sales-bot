/**
 * Короткий сигнал о новом заказе.
 *
 * Звук синтезируем осциллятором, а не проигрываем файл: два тона по десятой
 * доле секунды — это несколько строк кода вместо лишнего запроса за mp3.
 *
 * Браузеры не дают звучать странице, с которой ещё не взаимодействовали, —
 * обойти это нельзя и не нужно. Поэтому `AudioContext` создаётся при первом
 * клике по панели. Пока по ней не кликнули, карточка приходит молча: это не
 * поломка, а правило браузера.
 */
let context: AudioContext | null = null;

export function armSound() {
  if (context) return;
  const Ctor = window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return;
  try {
    context = new Ctor();
  } catch {
    context = null;
  }
}

/** Два коротких тона вверх — «дзинь-дзинь», негромко. */
export function chime() {
  if (!context) return;
  if (context.state === "suspended") void context.resume();

  const now = context.currentTime;
  for (const [index, frequency] of [880, 1180].entries()) {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequency;

    const start = now + index * 0.12;
    // Не прямоугольник, а плавное затухание: резкий обрыв слышен щелчком.
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.08, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.11);

    oscillator.connect(gain).connect(context.destination);
    oscillator.start(start);
    oscillator.stop(start + 0.12);
  }
}
