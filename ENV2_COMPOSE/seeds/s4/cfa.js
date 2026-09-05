// CFA (Contacts / Fund Accounts, MongoDB 7.0) seed for the Env2 arena. Run with:
//   mongosh <connection-string> cfa.js
//
// Ground truth: cfa/internal/database/migrations/*.go (collections + indexes: no unique index exists
// anywhere in this codebase -- contacts/fund_accounts/hash_lookup all rely on application code for
// dedup, confirmed by reading all 4 migration files), cfa/internal/fund_accounts/model.go (FundAccountModel,
// GenerateHash), cfa/internal/contacts/service.go (generateContactHash), cfa/internal/hashlookup/model.go.
//
// VERDICT (from the research pass): direct mongosh seeding is correct and sufficient for contacts,
// fund_accounts (bank_account + vpa types) and hash_lookup -- no live CFA API call is required --
// PROVIDED hash values are computed with the exact real algorithm (SHA3-256, i.e. NIST FIPS 202,
// via golang.org/x/crypto/sha3 -- NOT Keccak-256) and hash_lookup rows are written by hand alongside
// each doc, since MongoDB enforces zero uniqueness here (confirmed: no unique index on any of the
// 3 collections).
//
// Because mongosh's JS sandbox does not reliably expose Node's crypto/sha3 module, all hash values
// below were PRECOMPUTED with Python's hashlib.sha3_256 (same NIST SHA3-256 standard as Go's
// golang.org/x/crypto/sha3.Sum256) against the exact input strings the real Go code would produce,
// reproducing:
//   - fund_accounts: GenerateHash() (model.go:289-328) -- hashStr = "<merchantID>|contact|<contactID>|<accountType>|<details>"
//     where details for bank_account = "<accountNumber stripped>|<IFSC upper stripped>|<name stripped w/ -&'._()/ allowed>"
//     and for vpa = lowercase("<username, near-unstrippable due to the real regex bug>|<handle stripped>")
//   - contacts: generateContactHash() (service.go:215-235) -- SHA3-256 of the Go-compact-JSON-marshaled
//     {contact,email,merchantID,name,referenceID,type} map with alphabetically-sorted keys, no spaces.
//
// IDs: CFA's app-level "id" field (NOT Mongo's own _id, which the app never queries by) is a 14-char
// string from goutils/uniqueid in production. This seed uses 14-char "ARENA..." ids instead --
// functionally identical, since all CFA queries filter on the app-level "id" field.
//
// Fixture design note: fund account ARENAFAX000004 is BOTH inactive AND ends in account number
// suffix 9999 -- deliberately overlapping with shield_fixtures.json's scripted block-by-suffix rule,
// so a payout attempt against it should be rejected twice over (FAV inactive-account check, and if
// that were bypassed, Shield's block rule). See SYNTHETIC_FIXTURE_SPEC.md for the full rationale.

db = db.getSiblingDB('cfa');

const now = Date.now();

// ---------------------------------------------------------------------------
// contacts
// ---------------------------------------------------------------------------
const contacts = [
  {
    id: "ARENACO0000001", name: "Arena Vendor One", email: "vendor1@arena.test", contact: "+919900000001",
    type: "vendor", reference_id: "ARENA-VENDOR-001", notes: {}, merchant_id: "ARENAM00000001",
    active: true, created_at: now, updated_at: now,
    hash: "ed5a1880cceafefba2c1783d80f3b2e7eb65a02cfa408a289c1ea8f860272c2e"
  },
  {
    id: "ARENACO0000002", name: "Arena Vendor Two", email: "vendor2@arena.test", contact: "+919900000002",
    type: "vendor", reference_id: "ARENA-VENDOR-002", notes: {}, merchant_id: "ARENAM00000002",
    active: true, created_at: now, updated_at: now,
    hash: "a49ab44bd71f2657200e12d99635bcecfe6379ea701d23c94f91d36259e9fe95"
  },
  {
    id: "ARENACO0000003", name: "Arena Vendor Three VPA", email: "vendor3@arena.test", contact: "+919900000003",
    type: "vendor", reference_id: "ARENA-VENDOR-003", notes: {}, merchant_id: "ARENAM00000003",
    active: true, created_at: now, updated_at: now,
    hash: "1cf2a7d196c2309b777b14efbe48393d920cc5d54828b862d8650da9d804ac41"
  },
  {
    id: "ARENACO0000004", name: "Arena Vendor Inactive", email: "vendor4@arena.test", contact: "+919900000004",
    type: "vendor", reference_id: "ARENA-VENDOR-004", notes: {}, merchant_id: "ARENAM00000001",
    active: true, created_at: now, updated_at: now,
    hash: "9d041d4644642db735fb00cb3d404cab874056545bb892696e9ac153b6755be9"
  },
];

contacts.forEach(c => {
  db.contacts.updateOne({ id: c.id }, { $setOnInsert: c }, { upsert: true });
});

// ---------------------------------------------------------------------------
// fund_accounts (2 bank, 1 vpa, 1 inactive bank -- per task spec)
// ---------------------------------------------------------------------------
const fundAccounts = [
  {
    id: "ARENAFAX000001", contact_id: "ARENACO0000001", merchant_id: "ARENAM00000001",
    account_type: "bank_account",
    bank_account: { id: "ARENABKX000001", ifsc: "ARNA0000001", bank_name: "ARENA BANK", name: "Arena Vendor One", account_number: "2323230000000101" },
    active: true, created_at: now, updated_at: now,
    hash: "f59107936c4859e7a4a4f3a94851395b47d1c67110faa769b7d77bae401dfd66"
  },
  {
    id: "ARENAFAX000002", contact_id: "ARENACO0000002", merchant_id: "ARENAM00000002",
    account_type: "bank_account",
    bank_account: { id: "ARENABKX000002", ifsc: "ARNA0000001", bank_name: "ARENA BANK", name: "Arena Vendor Two", account_number: "2323230000000201" },
    active: true, created_at: now, updated_at: now,
    hash: "c8e6a0d8fb52c8b4b037382c474cd261a32e593dec67471161ea11aebc53232f"
  },
  {
    id: "ARENAFAX000003", contact_id: "ARENACO0000003", merchant_id: "ARENAM00000003",
    account_type: "vpa",
    vpa: { id: "ARENAVPAX000003", handle: "arenabank", username: "arena.user" },
    active: true, created_at: now, updated_at: now,
    hash: "d41f5d5c0e442777d70bb21ed6d5d7d0011db82a01068374b51333505b7d14b4"
  },
  {
    // Deliberately INACTIVE and ending 9999 -- see file header note.
    id: "ARENAFAX000004", contact_id: "ARENACO0000004", merchant_id: "ARENAM00000001",
    account_type: "bank_account",
    bank_account: { id: "ARENABKX000004", ifsc: "ARNA0000001", bank_name: "ARENA BANK", name: "Arena Vendor Inactive", account_number: "2323230000009999" },
    active: false, created_at: now, updated_at: now,
    hash: "674dcf8b2d6296b21f00070d6c368ad23586539a9ceb54553f67e8ee11ae28ff"
  },
];

fundAccounts.forEach(fa => {
  db.fund_accounts.updateOne({ id: fa.id }, { $setOnInsert: fa }, { upsert: true });
});

// ---------------------------------------------------------------------------
// hash_lookup -- one row per (hash, entity_type) -> entity_id. No unique index exists (confirmed),
// so this seed is the only thing preventing duplicate rows -- keep it as the single source of truth
// for what's been inserted.
// ---------------------------------------------------------------------------
const hashLookups = [
  ...contacts.map((c, i) => ({ id: `ARENAHL000000${i + 1}`, hash: c.hash, entity_id: c.id, entity_type: "contacts", created_at: now, updated_at: now })),
  ...fundAccounts.map((fa, i) => ({ id: `ARENAHL000010${i + 1}`, hash: fa.hash, entity_id: fa.id, entity_type: "fund_accounts", created_at: now, updated_at: now })),
];

hashLookups.forEach(hl => {
  db.hash_lookup.updateOne({ hash: hl.hash, entity_type: hl.entity_type }, { $setOnInsert: hl }, { upsert: true });
});

print("CFA seed complete: " + contacts.length + " contacts, " + fundAccounts.length + " fund_accounts, " + hashLookups.length + " hash_lookup rows.");
