/** Arbitration between the transport and the solo players.
 *
 *  Two things can make sound: the transport, which plays a whole timeline with
 *  several clips at once, and a solo `Player`, which auditions one file. Only
 *  one of them should be audible, and the rule lives here rather than in two
 *  components each guessing what the other is doing.
 */

type Stopper = () => void;

let stopTransport: Stopper | null = null;
let stopSolo: Stopper | null = null;

/** The transport registers once, on mount. */
export function registerTransport(stop: Stopper): () => void {
  stopTransport = stop;
  return () => {
    if (stopTransport === stop) stopTransport = null;
  };
}

/** A solo player is about to start: silence the transport and any other solo. */
export function claimSolo(stop: Stopper): void {
  stopTransport?.();
  if (stopSolo && stopSolo !== stop) stopSolo();
  stopSolo = stop;
}

export function releaseSolo(stop: Stopper): void {
  if (stopSolo === stop) stopSolo = null;
}

/** The transport is about to start: silence whichever solo is playing. */
export function claimTransport(): void {
  stopSolo?.();
  stopSolo = null;
}
