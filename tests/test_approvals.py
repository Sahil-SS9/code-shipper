"""STOP B: the approvals engine.

T-UNIT-POLICY-* (grammar), T-UNIT-EVAL-* (strictest-wins, renames, protected files, observe),
T-ADV-01/16/17 (a PR cannot lower its own bar; an agent cannot be set up to approve itself),
T-SCN-05 (observe vs enforce), C13 (effective config) and D15d (tighten-only).

The grammar tests are mostly refusal tests, because that is where the risk is: a permissive parser
turns a typo in a condition into an approval rule that passes everything.
"""

from __future__ import annotations

import unittest

from code_shipper.approvals.comment import MARKER, find_existing, render, should_update
from code_shipper.approvals.evaluate import Facts, evaluate, refuses_agent_approval
from code_shipper.approvals.policy import PolicyError, glob_match, parse
from code_shipper.approvals.shared import effective, merge


def policy_of(text: str, *, source: str = "") -> "object":
    return parse(text, source=source)


BASE = """
version: 1
approvals:
  - count: 1
    approvers: [alice, bob]
"""


class GrammarTests(unittest.TestCase):
    """T-UNIT-POLICY-01..20."""

    def test_a_minimal_policy_parses(self) -> None:
        policy = policy_of(BASE)
        self.assertEqual(len(policy.rules), 1)
        self.assertEqual(policy.rules[0].count, 1)
        self.assertEqual(policy.rules[0].approvers, ("alice", "bob"))
        self.assertFalse(policy.rules[0].observe)

    def test_an_unknown_top_level_key_is_refused(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            policy_of("version: 1\naproovals: []\n")
        self.assertIn("aproovals", str(caught.exception))

    def test_an_unknown_approval_key_is_refused(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            policy_of("version: 1\napprovals:\n  - count: 1\n    approvers: [a]\n    whem: {}\n")
        self.assertIn("whem", str(caught.exception))

    def test_a_duplicate_top_level_key_is_refused(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            policy_of("version: 1\nversion: 2\n")
        self.assertIn("duplicate", str(caught.exception))

    def test_a_typo_in_a_fact_name_is_refused(self) -> None:
        # a misspelled condition would silently drop the restriction
        with self.assertRaises(PolicyError) as caught:
            policy_of(BASE + "    when:\n      touch_path: [\"src/**\"]\n")
        self.assertIn("touch_path", str(caught.exception))

    def test_a_known_fact_is_accepted(self) -> None:
        policy = policy_of(BASE + "    when:\n      touches_path: [\"src/**\"]\n")
        self.assertEqual(policy.rules[0].when["touches_path"], ["src/**"])

    def test_a_bad_glob_is_refused(self) -> None:
        with self.assertRaises(PolicyError):
            policy_of(BASE + "    files: [\"src/[a\"]\n")

    def test_a_negative_count_is_refused(self) -> None:
        with self.assertRaises(PolicyError):
            policy_of("version: 1\napprovals:\n  - count: -1\n    approvers: [a]\n")

    def test_a_non_integer_count_is_refused(self) -> None:
        with self.assertRaises(PolicyError):
            policy_of("version: 1\napprovals:\n  - count: two\n    approvers: [a]\n")

    def test_seats_without_count_is_refused(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            policy_of("version: 1\napprovals:\n  - count: 0\n    approvers: [a, b]\n    seats: 2\n")
        self.assertIn("never be satisfied", str(caught.exception))

    def test_seats_exceeding_approvers_is_refused(self) -> None:
        with self.assertRaises(PolicyError):
            policy_of("version: 1\napprovals:\n  - count: 3\n    approvers: [a]\n    seats: 2\n")

    def test_observe_must_be_boolean(self) -> None:
        with self.assertRaises(PolicyError):
            policy_of(BASE + "    observe: maybe\n")

    def test_mode_must_be_all_or_any(self) -> None:
        with self.assertRaises(PolicyError):
            policy_of(BASE + "    mode: sometimes\n")

    def test_an_approval_rule_missing_count_is_refused(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            policy_of("version: 1\napprovals:\n  - approvers: [a]\n")
        self.assertIn("count", str(caught.exception))

    def test_ancestors_of_the_wield_syntax_are_not_required(self) -> None:
        # anchors are not in this grammar, and must not be silently skipped either
        with self.assertRaises(PolicyError):
            policy_of("version: 1\napprovals: &anchor\n  - count: 1\n    approvers: [a]\n")

    def test_an_empty_file_is_an_empty_policy(self) -> None:
        policy = policy_of("")
        self.assertEqual(policy.rules, ())
        self.assertEqual(policy.version, 1)

    def test_comments_are_ignored(self) -> None:
        policy = policy_of("# a policy\nversion: 1  # the version\n" + BASE.split("version: 1\n")[1])
        self.assertEqual(len(policy.rules), 1)

    def test_a_hash_inside_quotes_survives(self) -> None:
        policy = policy_of("version: 1\napprovals:\n  - count: 1\n    approvers: [\"a#b\"]\n")
        self.assertEqual(policy.rules[0].approvers, ("a#b",))

    def test_malformed_yaml_is_not_silently_accepted(self) -> None:
        with self.assertRaises(PolicyError):
            policy_of("this is not a policy at all\n")

    def test_protected_paths_are_parsed(self) -> None:
        policy = policy_of(BASE + "protected_paths:\n  - .github/**\n  - \"**/secrets/**\"\n")
        self.assertEqual(policy.protected_paths, (".github/**", "**/secrets/**"))


class GlobTests(unittest.TestCase):
    def test_star_does_not_cross_a_separator(self) -> None:
        self.assertTrue(glob_match("src/*.py", "src/a.py"))
        self.assertFalse(glob_match("src/*.py", "src/nested/a.py"))

    def test_double_star_crosses(self) -> None:
        self.assertTrue(glob_match("src/**/*.py", "src/a/b/c.py"))
        self.assertTrue(glob_match(".github/**", ".github/workflows/ci.yml"))


class EvaluateTests(unittest.TestCase):
    """T-UNIT-EVAL-01..15."""

    def test_one_approval_satisfies_a_one_rule(self) -> None:
        verdict = evaluate(policy_of(BASE), Facts(approvals=("alice",)))
        self.assertTrue(verdict.satisfied)
        self.assertEqual(verdict.required, 1)

    def test_no_approval_does_not_satisfy(self) -> None:
        verdict = evaluate(policy_of(BASE), Facts(approvals=()))
        self.assertFalse(verdict.satisfied)
        self.assertEqual(verdict.required, 1)
        self.assertEqual(verdict.present, 0)

    def test_an_approver_not_in_the_list_does_not_count(self) -> None:
        verdict = evaluate(policy_of(BASE), Facts(approvals=("mallory",)))
        self.assertFalse(verdict.satisfied)

    def test_strictest_wins_regardless_of_order(self) -> None:
        # a broad one-reviewer rule must not undercut a narrow three-reviewer rule
        text = """
version: 1
approvals:
  - count: 1
    approvers: [alice, bob, carol]
  - count: 3
    approvers: [alice, bob, carol]
"""
        verdict = evaluate(policy_of(text), Facts(approvals=("alice",)))
        self.assertFalse(verdict.satisfied)
        self.assertEqual(verdict.required, 3)

    def test_strictest_wins_when_the_narrow_rule_comes_first(self) -> None:
        text = """
version: 1
approvals:
  - count: 3
    approvers: [alice, bob, carol]
  - count: 1
    approvers: [alice, bob, carol]
"""
        verdict = evaluate(policy_of(text), Facts(approvals=("alice",)))
        self.assertEqual(verdict.required, 3)

    def test_a_rule_scoped_to_files_does_not_apply_elsewhere(self) -> None:
        text = BASE + "  - count: 3\n    approvers: [alice, bob, carol]\n    files: [\"auth/**\"]\n"
        verdict = evaluate(policy_of(text), Facts(changed_files=("docs/a.md",), approvals=("alice",)))
        self.assertTrue(verdict.satisfied)
        self.assertEqual(verdict.required, 1)

    def test_a_rule_scoped_to_files_applies_when_touched(self) -> None:
        text = BASE + "  - count: 3\n    approvers: [alice, bob, carol]\n    files: [\"auth/**\"]\n"
        verdict = evaluate(policy_of(text), Facts(changed_files=("auth/x.py",), approvals=("alice",)))
        self.assertFalse(verdict.satisfied)
        self.assertEqual(verdict.required, 3)

    def test_a_rename_counts_under_both_paths(self) -> None:
        text = BASE + "  - count: 2\n    approvers: [alice, bob]\n    files: [\"auth/**\"]\n"
        facts = Facts(changed_files=("lib/renamed.py",), previous_paths=("auth/original.py",),
                      approvals=("alice",))
        self.assertFalse(evaluate(policy_of(text), facts).satisfied)

    def test_observe_never_blocks(self) -> None:
        text = BASE + "  - count: 3\n    approvers: [a, b, c]\n    observe: true\n"
        verdict = evaluate(policy_of(text), Facts(approvals=()))
        # the enforcing rule still needs one, but the observe rule cannot add a block
        self.assertEqual(verdict.required, 1)

    def test_a_fully_observe_policy_blocks_nothing(self) -> None:
        text = """
version: 1
approvals:
  - count: 5
    approvers: [a, b, c, d, e]
    observe: true
"""
        verdict = evaluate(policy_of(text), Facts(approvals=()))
        self.assertTrue(verdict.observe_only)
        self.assertTrue(verdict.satisfied)

    def test_no_applicable_rule_is_a_satisfied_zero_not_an_approval_to_claim(self) -> None:
        text = BASE + "    files: [\"auth/**\"]\n"
        verdict = evaluate(policy_of(text), Facts(changed_files=("docs/a.md",)))
        self.assertTrue(verdict.satisfied)
        self.assertEqual(verdict.required, 0)

    def test_a_policy_with_no_rules_cannot_evaluate(self) -> None:
        verdict = evaluate(policy_of("version: 1\n"), Facts())
        self.assertFalse(verdict.evaluated)
        self.assertFalse(verdict.satisfied)

    def test_no_policy_means_no_approval(self) -> None:
        verdict = evaluate(None, Facts(approvals=("alice",)))
        self.assertFalse(verdict.evaluated)
        self.assertFalse(verdict.satisfied)

    def test_an_author_can_be_excluded_by_a_when_clause(self) -> None:
        text = BASE + "    when:\n      author: [renovate]\n"
        verdict = evaluate(policy_of(text), Facts(author="renovate", approvals=()))
        self.assertFalse(verdict.satisfied)

    def test_draft_state_can_be_required(self) -> None:
        text = BASE + "    when:\n      draft: false\n"
        verdict = evaluate(policy_of(text), Facts(draft=True, approvals=()))
        # the rule does not apply to a draft, so nothing is required and nothing blocks
        self.assertEqual(verdict.required, 0)


class AdversarialTests(unittest.TestCase):
    """T-ADV-01, 16, 17: the ways the bar gets lowered by accident."""

    def test_a_policy_read_from_the_pr_is_refused_when_the_pr_edits_it(self) -> None:
        # T-ADV-01: a PR cannot lower its own bar to zero
        policy = policy_of("version: 1\napprovals:\n  - count: 0\n    approvers: [a]\n", source="pr")
        verdict = evaluate(policy, Facts(edits_policy=True, approvals=()))
        self.assertFalse(verdict.evaluated)
        self.assertFalse(verdict.satisfied)
        self.assertIn("read from the PR", verdict.errors[0])

    def test_the_same_policy_from_the_base_branch_is_applied(self) -> None:
        policy = policy_of("version: 1\napprovals:\n  - count: 1\n    approvers: [a]\n", source="base")
        verdict = evaluate(policy, Facts(edits_policy=True, approvals=()))
        self.assertTrue(verdict.evaluated)

    def test_agent_approval_with_single_identity_is_refused_at_parse(self) -> None:
        # T-ADV-16
        with self.assertRaises(PolicyError) as caught:
            policy_of("version: 1\nallow_agent_approval: true\nagents:\n  mode: single\n")
        self.assertIn("allow_agent_approval", str(caught.exception))

    def test_agent_approval_without_any_identity_mode_is_refused(self) -> None:
        with self.assertRaises(PolicyError):
            policy_of("version: 1\nallow_agent_approval: true\n")

    def test_agent_approval_with_separated_identities_parses(self) -> None:
        policy = policy_of("version: 1\nallow_agent_approval: true\nagents:\n  mode: separated\n")
        self.assertTrue(policy.allow_agent_approval)

    def test_the_runtime_check_matches_the_parse_check(self) -> None:
        # T-ADV-17's identity half: a policy arriving from elsewhere is checked again
        policy = policy_of("version: 1\nallow_agent_approval: false\n")
        self.assertEqual(refuses_agent_approval(policy), (False, ""))
        policy.allow_agent_approval = True
        refused, why = refuses_agent_approval(policy)
        self.assertTrue(refused)
        self.assertIn("separated identities", why)

    def test_an_agent_approval_never_satisfies_a_rule(self) -> None:
        # the agent is not in the approvers list, and even if it were, approval comes from a human
        policy = policy_of("version: 1\napprovals:\n  - count: 1\n    approvers: [alice]\n")
        verdict = evaluate(policy, Facts(approvals=("code-shipper-bot",)))
        self.assertFalse(verdict.satisfied)


class SharedSettingsTests(unittest.TestCase):
    """D15d: a repo may tighten but never loosen."""

    def test_a_repo_cannot_turn_agent_approval_on(self) -> None:
        central = policy_of("version: 1\nallow_agent_approval: false\nagents:\n  mode: separated\n")
        local = policy_of("version: 1\nallow_agent_approval: true\nagents:\n  mode: separated\n")
        merged = merge(central, local, repo="o/r")
        self.assertFalse(merged.policy.allow_agent_approval)
        self.assertTrue(merged.refused, "the loosening must be refused, not silently dropped")
        self.assertIn("allow_agent_approval", merged.refused[0])

    def test_a_repo_may_turn_agent_approval_off(self) -> None:
        central = policy_of("version: 1\nallow_agent_approval: true\nagents:\n  mode: separated\n")
        local = policy_of("version: 1\nallow_agent_approval: false\nagents:\n  mode: separated\n")
        merged = merge(central, local)
        self.assertFalse(merged.policy.allow_agent_approval)
        self.assertIn("tightened", merged.provenance["allow_agent_approval"])

    def test_a_repo_cannot_drop_a_protected_path(self) -> None:
        central = policy_of("version: 1\nprotected_paths:\n  - .github/**\n  - \"**/secrets/**\"\n")
        local = policy_of("version: 1\nprotected_paths:\n  - .github/**\n")
        merged = merge(central, local)
        self.assertIn(".github/**", merged.policy.protected_paths)
        self.assertIn("**/secrets/**", merged.policy.protected_paths)
        self.assertTrue(any("dropped" in reason for reason in merged.refused))

    def test_a_repo_may_add_a_protected_path(self) -> None:
        central = policy_of("version: 1\nprotected_paths:\n  - .github/**\n")
        local = policy_of("version: 1\nprotected_paths:\n  - .github/**\n  - \"infra/**\"\n")
        merged = merge(central, local)
        self.assertIn("infra/**", merged.policy.protected_paths)
        self.assertFalse(merged.refused)

    def test_a_repo_cannot_raise_a_budget(self) -> None:
        central = policy_of("version: 1\nbudget:\n  rounds: 2\n")
        local = policy_of("version: 1\nbudget:\n  rounds: 9\n")
        merged = merge(central, local)
        self.assertEqual(merged.policy.budget["rounds"], 2)
        self.assertTrue(any("budget" in reason for reason in merged.refused))

    def test_a_repo_may_lower_a_budget(self) -> None:
        central = policy_of("version: 1\nbudget:\n  rounds: 5\n")
        local = policy_of("version: 1\nbudget:\n  rounds: 2\n")
        merged = merge(central, local)
        self.assertEqual(merged.policy.budget["rounds"], 2)

    def test_a_repo_cannot_broaden_its_scope(self) -> None:
        central = policy_of("version: 1\nscope:\n  allowed: [\"src/**\"]\n")
        local = policy_of("version: 1\nscope:\n  allowed: [\"**\"]\n")
        merged = merge(central, local)
        self.assertEqual(merged.policy.scope["allowed"], ["src/**"])

    def test_a_repo_cannot_reenable_a_disabled_trigger(self) -> None:
        central = policy_of("version: 1\ntrigger:\n  enabled: false\n")
        local = policy_of("version: 1\ntrigger:\n  enabled: true\n")
        merged = merge(central, local)
        self.assertIs(merged.policy.trigger["enabled"], False)
        self.assertTrue(merged.refused)

    def test_a_repo_cannot_collapse_separated_identities(self) -> None:
        central = policy_of("version: 1\nagents:\n  mode: separated\n")
        local = policy_of("version: 1\nagents:\n  mode: single\n")
        merged = merge(central, local)
        self.assertEqual(merged.policy.agents["mode"], "separated")

    def test_non_security_keys_may_be_set_freely(self) -> None:
        central = policy_of("version: 1\napprovals:\n  - count: 1\n    approvers: [a]\n")
        local = policy_of("version: 1\napprovals:\n  - count: 2\n    approvers: [a, b]\n")
        merged = merge(central, local)
        self.assertEqual(len(merged.policy.rules), 1)
        self.assertEqual(merged.provenance["approvals"], "repo")
        self.assertFalse(merged.refused)

    def test_a_repo_without_local_settings_inherits_central(self) -> None:
        central = policy_of(BASE)
        merged = merge(central, None)
        self.assertEqual(len(merged.policy.rules), 1)
        self.assertEqual(merged.provenance["approvals"], "central")

    def test_the_effective_preview_shows_provenance(self) -> None:
        central = policy_of("version: 1\nallow_agent_approval: false\nbudget:\n  rounds: 2\n")
        local = policy_of("version: 1\nbudget:\n  rounds: 9\n")
        text = effective(merge(central, local, repo="o/r"))
        self.assertIn("allow_agent_approval", text)
        self.assertIn("source", text)
        self.assertIn("refused", text)
        self.assertIn("budget", text)


class CommentTests(unittest.TestCase):
    """FR-A5: a status comment a reviewer can act on."""

    def test_a_shortfall_names_what_is_missing(self) -> None:
        policy = policy_of(BASE)
        verdict = evaluate(policy, Facts(approvals=()))
        comment = render(verdict, facts=Facts(approvals=()), policy=policy)
        self.assertIn("1 more approval(s) needed", comment.body)
        self.assertIn("alice", comment.body)
        self.assertEqual(comment.state, "pending")

    def test_an_approved_comment_says_approved(self) -> None:
        policy = policy_of(BASE)
        facts = Facts(approvals=("alice",))
        comment = render(evaluate(policy, facts), facts=facts, policy=policy)
        self.assertEqual(comment.state, "approved")
        self.assertIn("Approved", comment.body)

    def test_observe_is_never_readable_as_approved(self) -> None:
        text = "version: 1\napprovals:\n  - count: 2\n    approvers: [a, b]\n    observe: true\n"
        policy = policy_of(text)
        facts = Facts(approvals=())
        comment = render(evaluate(policy, facts), facts=facts, policy=policy)
        self.assertEqual(comment.state, "observe")
        self.assertIn("Observe only", comment.body)

    def test_an_unevaluable_policy_says_so(self) -> None:
        comment = render(evaluate(None, Facts()), facts=Facts(), policy=None)
        self.assertEqual(comment.state, "error")
        self.assertIn("Cannot evaluate", comment.body)

    def test_the_body_carries_the_marker(self) -> None:
        policy = policy_of(BASE)
        comment = render(evaluate(policy, Facts()), facts=Facts(), policy=policy)
        self.assertIn(MARKER, comment.body)

    def test_an_existing_comment_is_found_by_marker(self) -> None:
        class C:
            def __init__(self, body):
                self.body = body

        found = find_existing([C("hello"), C(MARKER + "\n## Approvals")])
        self.assertIsNotNone(found)
        self.assertIn(MARKER, found.body)

    def test_no_comment_returns_none(self) -> None:
        class C:
            body = "hello"

        self.assertIsNone(find_existing([C()]))

    def test_an_unchanged_body_is_not_reposted(self) -> None:
        policy = policy_of(BASE)
        comment = render(evaluate(policy, Facts()), facts=Facts(), policy=policy)
        self.assertFalse(should_update(comment.body, comment))
        self.assertTrue(should_update(None, comment))

    def test_the_comment_states_that_the_tool_never_approves(self) -> None:
        policy = policy_of(BASE)
        comment = render(evaluate(policy, Facts()), facts=Facts(), policy=policy)
        self.assertIn("never approves", comment.body)


if __name__ == "__main__":
    unittest.main()
