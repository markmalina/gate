import crypto from "node:crypto";

export const OMARK_FOOD_CONSENT_V2 = "food-consent-v2";
export const OMARK_FOOD_PURPOSE_V2 = "food_owner_text_canary_v2";

export const OMARK_FOOD_TEXT_CAPABILITIES = Object.freeze([
  "food.text",
  "food.inference",
  "food.nutrition_estimate",
  "food.day_summary",
  "food.omark_suggestion",
  "food.assisted_processing",
]);

const CAPABILITIES = new Set(OMARK_FOOD_TEXT_CAPABILITIES);
const REQUIRED_POLICY_FIELDS = Object.freeze([
  "controller_operator",
  "processors_readers",
  "retention",
  "backup_log_policy",
  "delete_sla",
  "assisted_mode_access",
  "tombstone_fields",
]);
const REF_RE =
  /^(?:installation|conversation|cube):[A-Za-z][A-Za-z0-9._~-]{7,127}$/u;
const DIGEST_RE = /^[0-9a-f]{64}$/u;

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  }
  return value;
}

function digest(value) {
  return crypto
    .createHash("sha256")
    .update(JSON.stringify(canonical(value)), "utf8")
    .digest("hex");
}

function fail(code, message) {
  const error = new Error(message);
  error.code = code;
  throw error;
}

function normalizeCapabilities(input) {
  if (!Array.isArray(input) || input.length === 0) {
    throw new TypeError("Food consent v2 requires closed capabilities");
  }
  const value = [...new Set(input)].sort();
  if (value.some((capability) => !CAPABILITIES.has(capability))) {
    throw new TypeError("Food consent v2 capability is not reviewed");
  }
  if (value.some((capability) => capability.includes("photo"))) {
    fail(
      "AQUB_FOOD_PHOTO_NOT_AUTHORIZED",
      "Photo remains disabled until Work G",
    );
  }
  return Object.freeze(value);
}

function policyCompleteness(policy) {
  const unresolved = REQUIRED_POLICY_FIELDS.filter((field) => {
    const value = policy?.[field];
    return value === null || value === undefined || value === "";
  });
  return Object.freeze(unresolved);
}

export function createOmarkFoodConsentV2Profile({
  installationRef,
  conversationRef,
  cubeRef,
  routeGeneration,
  policyVersion,
  policy = {},
  capabilities = OMARK_FOOD_TEXT_CAPABILITIES,
  mode = "default_off",
} = {}) {
  for (const value of [installationRef, conversationRef, cubeRef]) {
    if (!REF_RE.test(value ?? "")) {
      throw new TypeError("Food consent v2 route reference is invalid");
    }
  }
  if (!Number.isSafeInteger(routeGeneration) || routeGeneration < 1) {
    throw new TypeError("Food consent v2 route generation is invalid");
  }
  if (!/^[a-z0-9][a-z0-9._-]{2,95}$/u.test(policyVersion ?? "")) {
    throw new TypeError("Food consent v2 policy version is invalid");
  }
  if (!new Set(["default_off", "synthetic"]).has(mode)) {
    throw new TypeError("Food consent v2 mode is invalid");
  }
  const closedCapabilities = normalizeCapabilities(capabilities);
  const unresolvedPolicyFields = policyCompleteness(policy);
  const policyDigest = digest({ policyVersion, policy });
  const profile = {
    version: OMARK_FOOD_CONSENT_V2,
    purpose: OMARK_FOOD_PURPOSE_V2,
    spaceKind: "private_mini_group",
    installationRef,
    conversationRef,
    cubeRef,
    routeGeneration,
    policyVersion,
    policyDigest,
    capabilities: closedCapabilities,
    mode,
    policyComplete: unresolvedPolicyFields.length === 0,
    unresolvedPolicyFields,
  };
  return Object.freeze({ ...profile, digest: digest(profile) });
}

export function assertOmarkFoodConsentV2(profile, grant, required = []) {
  const capabilities = new Set(profile?.capabilities ?? []);
  if (
    profile?.version !== OMARK_FOOD_CONSENT_V2 ||
    profile?.purpose !== OMARK_FOOD_PURPOSE_V2 ||
    !DIGEST_RE.test(profile?.digest ?? "") ||
    !DIGEST_RE.test(profile?.policyDigest ?? "") ||
    profile?.policyComplete !== true ||
    (profile?.mode !== "synthetic" && profile?.mode !== "approved") ||
    grant?.status !== "granted" ||
    grant?.version !== profile.version ||
    grant?.digest !== profile.digest ||
    grant?.policyVersion !== profile.policyVersion ||
    !Number.isSafeInteger(grant?.authorityEpoch) ||
    grant.authorityEpoch < 1
  ) {
    fail(
      "AQUB_FOOD_CONSENT_V2_REQUIRED",
      "Complete, current, actor-bound Food consent v2 is required",
    );
  }
  for (const capability of required) {
    if (!CAPABILITIES.has(capability) || !capabilities.has(capability)) {
      fail(
        "AQUB_FOOD_CAPABILITY_REQUIRED",
        `Food capability is not granted: ${capability}`,
      );
    }
  }
  return true;
}

export function assertOmarkFoodTextOnly(content) {
  if (content?.kind !== "text") {
    fail(
      "AQUB_FOOD_PHOTO_NOT_AUTHORIZED",
      "Only text Food input is authorized before Work G",
    );
  }
  return true;
}

export function capabilityForDeliveryType(deliveryType) {
  const capability = {
    "food.clarification": "food.text",
    "food.fact_reply": "food.nutrition_estimate",
    "food.correction_reply": "food.inference",
    "food.day_summary": "food.day_summary",
    "food.omark_check": "food.omark_suggestion",
  }[deliveryType];
  if (!capability) {
    throw new TypeError("Food delivery type is not reviewed");
  }
  return capability;
}

export const OMARK_FOOD_POLICY_DECISIONS = Object.freeze({
  required: REQUIRED_POLICY_FIELDS,
  realDataEnabled: false,
});
