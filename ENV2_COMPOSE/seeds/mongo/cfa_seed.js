// seeds/mongo/cfa_seed.js
// CFA contacts + fund accounts for the 3 synthetic merchants. Run AFTER
// cfa-migrate, via `docker compose exec -T mongo-cfa mongosh cfa
// --username cfa_root --password "$(cat secrets/mongo_cfa_root_password.txt)"
// --authenticationDatabase admin < this file` (scripts/up.sh's seed step).
//
// TODO (explicit): CFA's real Mongo document shape (cfa/internal/**/model
// or equivalent) was not read in this pass -- collection/field names below
// are a best-effort skeleton based on the concept names BOM/findings use
// ("contacts", "fund_accounts"), matching the REST shape
// substitutes/monolith-stub's own contacts/fund_accounts endpoints use for
// consistency, NOT verified against CFA's real Mongo schema/indexes.
// Confirm before relying on this for anything beyond smoke-testing.

db = db.getSiblingDB("cfa");

db.contacts.insertMany([
  {
    _id: "cont_ARENA000001",
    merchant_id: "ARENA_M1",
    name: "Arena Beneficiary One",
    email: "beneficiary1@arena.test",
    contact: "9000000001",
    type: "employee",
    active: true,
    created_at: new Date(),
  },
  {
    _id: "cont_ARENA000002",
    merchant_id: "ARENA_M2",
    name: "Arena Beneficiary Two",
    email: "beneficiary2@arena.test",
    contact: "9000000002",
    type: "vendor",
    active: true,
    created_at: new Date(),
  },
  {
    _id: "cont_ARENA000003",
    merchant_id: "ARENA_M3",
    name: "Arena Beneficiary Three",
    email: "beneficiary3@arena.test",
    contact: "9000000003",
    type: "employee",
    active: true,
    created_at: new Date(),
  },
]);

db.fund_accounts.insertMany([
  {
    _id: "fa_ARENA000001",
    merchant_id: "ARENA_M1",
    contact_id: "cont_ARENA000001",
    account_type: "bank_account",
    active: true,
    bank_account: {
      ifsc: "RATN0000001",
      bank_name: "RBL Bank (arena)",
      account_number: "111000000000001",
      beneficiary_name: "Arena Beneficiary One",
    },
    created_at: new Date(),
  },
  {
    _id: "fa_ARENA000002",
    merchant_id: "ARENA_M2",
    contact_id: "cont_ARENA000002",
    account_type: "bank_account",
    active: true,
    bank_account: {
      ifsc: "RATN0000001",
      bank_name: "RBL Bank (arena)",
      account_number: "111000000000002",
      beneficiary_name: "Arena Beneficiary Two",
    },
    created_at: new Date(),
  },
  {
    _id: "fa_ARENA000003",
    merchant_id: "ARENA_M3",
    contact_id: "cont_ARENA000003",
    account_type: "vpa",
    active: true,
    vpa: {
      address: "arena.beneficiary3@arenabank",
    },
    created_at: new Date(),
  },
]);

print("cfa_seed.js: inserted " + db.contacts.countDocuments({}) + " contacts, " +
      db.fund_accounts.countDocuments({}) + " fund_accounts");
