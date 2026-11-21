/** Time constants the UI waits on; a module of their own so tests can shrink them. */

/** The title job runs after the first answer. Check back a few times while it is still "New chat". */
export const TITLE_REFRESH_DELAYS_MS = [1500, 4000, 8000]

/** Streamed tokens are applied to the screen in batches this far apart. */
export const TOKEN_BATCH_MS = 16
