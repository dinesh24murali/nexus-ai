/**
 * When to look for what the memory job stored. It runs after the answer, so it is not there yet
 * when the stream ends: the first check is soon, the last gives a slow model time.
 */
export const MEMORY_CHECK_DELAYS_MS = [1500, 4000, 9000, 20000]

/** The list read when a turn starts is reused if it is younger than this. */
export const SNAPSHOT_STALE_MS = 30_000
