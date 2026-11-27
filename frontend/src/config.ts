/**
 * Build-time frontend configuration (Vite `VITE_*` variables).
 *
 * The API does not expose its REGISTRATION_MODE, so a self-hosted instance running with
 * `REGISTRATION_MODE=open` must build the frontend with `VITE_REGISTRATION_MODE=open` to hide the
 * invite field. The default matches the API default.
 */
export type RegistrationMode = 'invite' | 'open'

export const config = {
  registrationMode: (import.meta.env.VITE_REGISTRATION_MODE === 'open'
    ? 'open'
    : 'invite') as RegistrationMode,
}
