/**
 * The puppet rig as the player needs it: the list of bones every puppet frame
 * carries. Read from shared/rig/puppet.json so it never drifts from the rig.
 *
 * Bundler-only (uses the @shared alias and a JSON import); the node unit tests
 * read the JSON from disk instead.
 */

import puppet from "@shared/rig/puppet.json";

/** Every bone name in shared/rig/puppet.json, in file order. */
export const PUPPET_BONES: readonly string[] = Object.freeze(Object.keys(puppet.bones));
