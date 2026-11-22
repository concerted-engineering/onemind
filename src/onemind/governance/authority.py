"""Authority verification separating local assertions from verified repository approvals."""

from __future__ import annotations

from typing import Any

from onemind.adapters.interfaces import AuthorityVerificationResult, AuthorityVerifier
from onemind.governance.policy import AuthorityPolicy
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import ActorType, AuthorityClass, RecordDict


class DefaultAuthorityVerifier(AuthorityVerifier):
    """Conservative, fail-safe authority verifier for OneMind v0.1-draft.

    Enforces:
    - Agent actors never originate effective governance decisions.
    - Every decision binds to exact target record_id and RFC 8785 canonical SHA-256 digest.
    - Local owner assertions are explicitly distinguished from verified repository approvals
      and never upgraded implicitly.
    - Unknown or incomplete authority modes never activate practices or applications.
    """

    def verify(
        self,
        decision: RecordDict,
        target: RecordDict | None,
        policy: Any,
    ) -> AuthorityVerificationResult:
        dec_id = str(decision.get("record_id", "<unknown>"))
        auth_policy = policy if isinstance(policy, AuthorityPolicy) else AuthorityPolicy()

        actor = decision.get("actor", {})
        actor_type = actor.get("type")
        if actor_type == ActorType.AGENT:
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=False,
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                effective_for_policy=False,
                reason="An agent/model actor must never originate a governance decision.",
            )

        if target is None:
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=False,
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                effective_for_policy=False,
                reason="Governance decision target record is missing.",
            )

        body: dict[str, Any] = decision.get("body", {})
        if target.get("kind") != body.get("target_kind"):
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=False,
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                effective_for_policy=False,
                reason=(
                    f"Target kind mismatch: decision cites {body.get('target_kind')!r}, "
                    f"target is {target.get('kind')!r}."
                ),
            )

        actual_sha = canonical_sha256(target)
        declared_sha = body.get("target_sha256")
        if actual_sha != declared_sha:
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=False,
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                effective_for_policy=False,
                reason=(
                    f"Target canonical SHA-256 mismatch: declared {declared_sha!r} != actual {actual_sha!r}."
                ),
            )

        authority = body.get("authority")
        proof = body.get("proof")
        if not isinstance(authority, dict) or not isinstance(proof, dict):
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=False,
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                effective_for_policy=False,
                reason="Governance decision missing structured 'authority' or 'proof' object.",
            )

        mode = authority.get("mode")
        subject = authority.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=False,
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                effective_for_policy=False,
                reason="Governance decision missing non-empty 'authority.subject'.",
            )

        if proof.get("revoked") is True or proof.get("stale") is True:
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=False,
                authority_class=AuthorityClass.INVALID_AUTHORITY,
                effective_for_policy=False,
                reason="Governance decision proof is marked stale or revoked.",
            )

        if mode == "local-owner-assertion":
            if proof.get("verified_repository_approval") is True:
                return AuthorityVerificationResult(
                    decision_record_id=dec_id,
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason=(
                        "Invalid local-owner-assertion: cannot claim verified_repository_approval=true."
                    ),
                )
            if actor_type != ActorType.HUMAN:
                return AuthorityVerificationResult(
                    decision_record_id=dec_id,
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason="A local-owner-assertion must be originated by a human actor.",
                )
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=True,
                authority_class=AuthorityClass.LOCAL_OWNER_ASSERTION,
                effective_for_policy=auth_policy.allow_local_assertions,
                reason=(
                    "Valid local-owner-assertion (not externally verified repository approval)."
                    if auth_policy.allow_local_assertions
                    else (
                        "Local-owner-assertion does not satisfy strict repository approval policy "
                        "(verified_repository_approval=false)."
                    )
                ),
                details={"subject": subject, "verified_repository_approval": False},
            )

        if mode == "verified-repository-approval":
            if proof.get("verified_repository_approval") is not True:
                return AuthorityVerificationResult(
                    decision_record_id=dec_id,
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason="Claimed verified-repository-approval lacks proof.verified_repository_approval=true.",
                )
            if auth_policy.require_protected_merge and proof.get("protected_branch") is not True:
                return AuthorityVerificationResult(
                    decision_record_id=dec_id,
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason="Verified repository approval requires proof.protected_branch=true.",
                )
            if not proof.get("review_commit") or not proof.get("merge_commit"):
                return AuthorityVerificationResult(
                    decision_record_id=dec_id,
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason="Verified repository approval requires both 'review_commit' and 'merge_commit'.",
                )
            if proof.get("reviewed_sha256") and proof.get("reviewed_sha256") != actual_sha:
                return AuthorityVerificationResult(
                    decision_record_id=dec_id,
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason="Proof reviewed_sha256 does not match target canonical SHA-256.",
                )
            reviewer = proof.get("reviewer", subject)
            if reviewer != subject:
                return AuthorityVerificationResult(
                    decision_record_id=dec_id,
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason=f"Proof reviewer {reviewer!r} does not match authority subject {subject!r}.",
                )
            if subject not in auth_policy.authorized_reviewers:
                return AuthorityVerificationResult(
                    decision_record_id=dec_id,
                    valid_proof=False,
                    authority_class=AuthorityClass.INVALID_AUTHORITY,
                    effective_for_policy=False,
                    reason=(
                        f"Reviewer {subject!r} is not in policy authorized_reviewers "
                        f"({sorted(auth_policy.authorized_reviewers)})."
                    ),
                )
            return AuthorityVerificationResult(
                decision_record_id=dec_id,
                valid_proof=True,
                authority_class=AuthorityClass.VERIFIED_REPOSITORY_APPROVAL,
                effective_for_policy=True,
                reason=f"Verified repository approval by authorized reviewer {subject!r}.",
                details={
                    "subject": subject,
                    "verified_repository_approval": True,
                    "review_commit": proof["review_commit"],
                    "merge_commit": proof["merge_commit"],
                },
            )

        return AuthorityVerificationResult(
            decision_record_id=dec_id,
            valid_proof=False,
            authority_class=AuthorityClass.INVALID_AUTHORITY,
            effective_for_policy=False,
            reason=f"Unknown or unsupported authority mode {mode!r}; failing safely (non-activation).",
        )


__all__ = ["AuthorityVerificationResult", "AuthorityVerifier", "DefaultAuthorityVerifier"]
