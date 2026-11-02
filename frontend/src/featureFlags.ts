/**
 * Feature flags. Flip + rebuild (`npm run build`) to toggle a feature
 * on or off across the whole frontend. Nothing fancy — no runtime
 * configuration, no backend involvement.
 */

/**
 * Performance & Targets module. When `false`:
 *   - The "Performance & Targets" Analytics hub card is hidden.
 *   - The Performance screen is unreachable (direct view routing is
 *     also gated).
 *   - The Order Assistant strip hides the target donut + the
 *     "All my targets" button (just shows Today / Week / Month cards).
 *
 * When `true`: everything renders normally.
 *
 * Backend endpoints remain registered (safe to leave running); the
 * frontend just doesn't call them. Nothing was deleted — flip the flag
 * when the redesign is ready to ship.
 */
export const TARGETS_FEATURE_ENABLED = false;
