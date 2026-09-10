"""Offline tests: the fresh-merchant ledger account-id namespace must be
collision-free and must never reuse a fixture id.

Regression cover for the M6 I5 defect: provisioner._ids_for used to build
acc_prefix from only the LAST TWO DIGITS of the merchant number
(``("ARENA" + str(num)[-2:])[:7].ljust(7, "0")``), i.e. 100 namespaces. Because
provision_funded_merchant inserted ledger accounts with
``ON CONFLICT (id) DO NOTHING`` and recorded the step ok regardless, a collision
left the fresh merchant with ZERO ledger accounts while the descriptor still
reported verified: true.

Nothing here touches docker, the arena or any database.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from red_loop import provisioner  # noqa: E402
from red_loop import provisioner_direct  # noqa: E402

# The active generated ledger seed: every id the M1/M2/M3 fixtures (and the
# ledger platform/pool accounts) occupy.
LEDGER_SEED = REPO / "ENV2_COMPOSE" / "seeds" / "generated" / "s4" / "ledger.sql"

CAMPAIGNS = 10000


def _seed_ids():
    """Every quoted ARENA*/baseline id literal in the generated ledger seed."""
    text = LEDGER_SEED.read_text()
    return {m.group(1) for m in re.finditer(r"'([A-Za-z0-9]{6,20})'", text)}


class LedgerAccPrefixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaigns = ["m6-campaign-%05d" % i for i in range(CAMPAIGNS)]
        cls.ids = [provisioner._ids_for(c, "attacker") for c in cls.campaigns]

    # -- collision freedom ---------------------------------------------------
    def test_distinct_campaigns_yield_distinct_acc_prefixes(self):
        nums = {d["num"] for d in self.ids}
        prefixes = {d["acc_prefix"] for d in self.ids}
        self.assertEqual(len(self.campaigns), CAMPAIGNS)
        # Any two campaigns that hash to the same `num` are the same merchant and
        # legitimately share a prefix; every DISTINCT num must get its own.
        self.assertEqual(len(prefixes), len(nums),
                         "acc_prefix is not injective in num: %d prefixes for %d nums"
                         % (len(prefixes), len(nums)))
        self.assertGreater(len(nums), CAMPAIGNS * 0.99, "hash range unexpectedly degenerate")

    def test_prefix_is_injective_over_the_whole_num_range(self):
        """Exhaustive-by-construction: sample the boundaries of the 9e6-wide range
        _ids_for draws from, including every carry point of the base36 encoding."""
        lo, hi = 90000000, 98999999
        probes = set()
        for n in (lo, lo + 1, hi - 1, hi):
            probes.add(n)
        for k in range(0, 6):
            for delta in (-1, 0, 1):
                n = lo + k * 36 ** 4 + delta
                if lo <= n <= hi:
                    probes.add(n)
        for step in range(0, 9000000, 9973):        # prime stride, ~903 samples
            probes.add(lo + step)
        seen = {}
        for n in sorted(probes):
            p = provisioner._ledger_acc_prefix(n)
            if p in seen:
                self.fail("prefix %s produced by both %d and %d" % (p, seen[p], n))
            seen[p] = n

    def test_encoding_never_silently_truncates(self):
        """The whole 7-digit space fits: max n7 (9_999_999) < 6 * 36**4, so `hi`
        stays in 0..5 and the encoding is lossless. The ValueError guard exists so
        that a future widening of the `num` range fails loudly instead of folding
        two merchants onto one prefix."""
        self.assertLess(10 ** 7 - 1, 6 * 36 ** 4)
        self.assertEqual(provisioner._ledger_acc_prefix(90000000), "ARENAG0000")
        # outside the Shared window (here: the Direct provisioner's 8xxxxxxx range)
        # the encoding would no longer be injective, so it refuses instead.
        with self.assertRaises(ValueError):
            provisioner._ledger_acc_prefix(80000000)

    # -- id geometry ---------------------------------------------------------
    def test_ids_fit_the_char14_ledger_columns(self):
        """ledger.accounts.id and ledger.account_details.id are CHAR(14)
        (ledger rx_migrations/20201001011117_create_accounts.go,
        20201022184734_create_account_details.go)."""
        for d in self.ids[:500]:
            p = d["acc_prefix"]
            self.assertEqual(len(p), provisioner.LEDGER_PREFIX_LEN, p)
            for i in provisioner.ledger_account_ids(p) + provisioner.ledger_account_detail_ids(p):
                self.assertEqual(len(i), provisioner.LEDGER_ID_LEN, i)
                self.assertRegex(i, r"^ARENA[G-L][0-9A-Z]{4}(AC|DT)0[1-4]$", i)

    def test_marker_char_is_outside_the_hex_alphabet(self):
        """The seed generator mints 'ARENA' + 9 uppercase HEX chars
        (ENV2_COMPOSE/seeds/generator/generate.py synthetic_id). A non-hex marker
        at index 5 makes a fixture collision structurally impossible."""
        for d in self.ids:
            self.assertIn(d["acc_prefix"][5], "GHIJKL", d["acc_prefix"])

    # -- fixture disjointness ------------------------------------------------
    def test_no_generated_id_collides_with_a_fixture_ledger_id(self):
        seed = _seed_ids()
        self.assertIn("ARENAPRACC0001", seed, "seed file did not parse as expected")
        self.assertIn("ARENAM1ACC0001", seed, "seed file did not parse as expected")
        for d in self.ids:
            p = d["acc_prefix"]
            for i in provisioner.ledger_account_ids(p) + provisioner.ledger_account_detail_ids(p):
                self.assertNotIn(i, seed, "generated ledger id %s collides with a fixture id" % i)

    def test_shared_and_direct_namespaces_cannot_overlap(self):
        direct = {provisioner_direct.ids_for_direct("m6-campaign-%05d" % i)["ledger_prefix"]
                  for i in range(2000)}
        shared = {d["acc_prefix"] for d in self.ids}
        self.assertFalse(direct & shared)

    # -- the old scheme is genuinely gone ------------------------------------
    def test_old_two_digit_scheme_would_have_collided(self):
        old = {("ARENA" + str(d["num"])[-2:])[:7].ljust(7, "0") for d in self.ids}
        self.assertLessEqual(len(old), 100, "sanity: the old scheme had at most 100 namespaces")
        self.assertGreater(len({d["acc_prefix"] for d in self.ids}), len(old) * 50)


if __name__ == "__main__":
    unittest.main()
