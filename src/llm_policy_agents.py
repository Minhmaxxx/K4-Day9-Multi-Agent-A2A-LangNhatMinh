"""Read-only, local-LLM policy deliberation agents for EC_POLICY_V2.

This module never mutates the supplied input or CSV data. The model receives a
small evidence packet produced by data agents, then proposer, critic and
finalizer roles deliberate on the primary issue. A public-policy resolver only
derives consequential fields (refund/action/evidence) from that LLM choice.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PRIMARY_ISSUES = (
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "unsupported_late_claim",
)


class PolicyDeliberationAgent:
    """Three local roles sharing one permitted model instance in LM Studio."""

    name = "policy_deliberation_agent"

    def __init__(self, base_url: str, model: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.token = token
        self.retry_count = 0

    def _request(self, system: str, prompt: str, eligible_primary: list[str] | None = None) -> str:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request_body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 120,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt + "\n/no_think"},
            ],
        }
        if eligible_primary:
            request_body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "eligible_policy_issue",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"primary_issue": {"type": "string", "enum": eligible_primary}},
                        "required": ["primary_issue"],
                        "additionalProperties": False,
                    },
                },
            }
        body = json.dumps(request_body).encode("utf-8")
        request = Request(f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as error:
            raise ValueError(f"LM Studio request failed: {error}") from error
        return payload["choices"][0]["message"]["content"]

    @staticmethod
    def _json_object(content: str, required_key: str) -> dict[str, Any]:
        decoder = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []
        for index, character in enumerate(content):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(content[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and required_key in value:
                candidates.append(value)
        if not candidates:
            raise ValueError(f"No JSON object containing {required_key}")
        return candidates[-1]

    @staticmethod
    def _facts(order: dict[str, str], product: dict[str, Any], payment: dict[str, Any], delivery: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_status": order["order_status"],
            "payment_total_brl": payment["payment_total_brl"],
            "payment_count": payment["payment_count"],
            "reconciled": payment["reconciled"],
            "freight_total_brl": payment["freight_total_brl"],
            "item_count": len(product["items"]),
            "seller_ids": product["seller_ids"],
            "category_count": len(product["category_names"]),
            "delivery_late": delivery["delivery_late"],
            "late_handoff_seller_ids": delivery["late_handoff_seller_ids"],
        }

    @staticmethod
    def _feasible_options(facts: dict[str, Any]) -> list[str]:
        if facts["order_status"] == "canceled" and facts["payment_total_brl"] > 0:
            return ["canceled_order_paid"]
        if facts["order_status"] == "unavailable" and facts["payment_total_brl"] > 0:
            return ["unavailable_order_paid"]
        if facts["delivery_late"] and facts["late_handoff_seller_ids"]:
            return ["late_delivery_seller"]
        if facts["delivery_late"]:
            return ["late_delivery_logistics"]
        if facts["payment_count"] >= 2 and facts["reconciled"] is True:
            return ["valid_split_payment"]
        if not facts["delivery_late"] and facts["reconciled"] is True:
            return ["unsupported_late_claim"]
        return []

    @staticmethod
    def _primary(candidate: dict[str, Any]) -> str:
        if set(candidate) != {"primary_issue"} or candidate["primary_issue"] not in PRIMARY_ISSUES:
            raise ValueError("Expected exactly one supported primary_issue")
        return candidate["primary_issue"]

    def _propose(self, facts: dict[str, Any]) -> str:
        prompt = (
            "Choose the one feasible EC_POLICY_V2 primary issue from the evidence packet. "
            "Priority: canceled_paid, unavailable_paid, late_seller, late_logistics, valid_split, unsupported_late. "
            "Return exactly {\"primary_issue\":\"...\"}. Allowed values: " + ", ".join(PRIMARY_ISSUES) +
            "\nEvidence packet:\n" + json.dumps(facts, separators=(",", ":"))
        )
        return self._primary(self._json_object(self._request("You are Policy Proposer. Output JSON only.", prompt), "primary_issue"))

    def _critique(self, facts: dict[str, Any], proposal: str, eligible: list[str]) -> dict[str, Any]:
        prompt = (
            "Audit the proposed EC_POLICY_V2 primary issue against the evidence packet. "
            "Return exactly {\"approved\":true_or_false,\"corrected_primary_issue\":\"...\",\"reason\":\"short\"}. "
            "corrected_primary_issue must be one of: " + ", ".join(PRIMARY_ISSUES) +
            "\nProposal: " + proposal + "\nPolicy Eligibility Tool response: " + json.dumps(eligible) +
            "\nEvidence packet:\n" + json.dumps(facts, separators=(",", ":"))
        )
        candidate = self._json_object(self._request("You are Policy Critic. Output JSON only.", prompt), "approved")
        if (set(candidate) != {"approved", "corrected_primary_issue", "reason"}
                or not isinstance(candidate["approved"], bool)
                or candidate["corrected_primary_issue"] not in PRIMARY_ISSUES
                or not isinstance(candidate["reason"], str)):
            raise ValueError("Policy Critic returned an invalid contract")
        return candidate

    def _finalize(
        self, facts: dict[str, Any], proposal: str, critique: dict[str, Any], eligible: list[str], correction: str = ""
    ) -> str:
        prompt = (
            "Resolve the final EC_POLICY_V2 primary issue using the evidence packet, proposal and critic review. "
            "Return exactly {\"primary_issue\":\"...\"}; allowed values: " + ", ".join(PRIMARY_ISSUES) +
            "\nProposal: " + proposal + "\nCritic: " + json.dumps(critique, separators=(",", ":")) +
            "\nPolicy Eligibility Tool response: " + json.dumps(eligible) +
            "\nEvidence packet:\n" + json.dumps(facts, separators=(",", ":"))
        )
        if correction:
            prompt += "\nSource verifier rejected your prior choice: " + correction
        return self._primary(self._json_object(
            self._request("You are Policy Finalizer. Output JSON only.", prompt, eligible), "primary_issue"
        ))

    def decide_with_trace(
        self,
        case_id: str,
        trace: Any,
        resolver: Any,
        order: dict[str, str],
        product: dict[str, Any],
        payment: dict[str, Any],
        delivery: dict[str, Any],
    ) -> dict[str, Any]:
        facts = self._facts(order, product, payment, delivery)
        eligible = self._feasible_options(facts)
        if not eligible:
            raise ValueError(f"{case_id}: no EC_POLICY_V2 primary issue is feasible from source facts")
        proposal: str | None = None
        for _ in range(3):
            try:
                proposal = self._propose(facts)
                break
            except ValueError:
                self.retry_count += 1
        if proposal is None:
            raise ValueError(f"{case_id}: policy proposer could not satisfy its JSON contract")
        trace.handoff(case_id, "policy_proposer", "policy_critic", {"primary_issue": proposal})
        critique: dict[str, Any] | None = None
        for _ in range(3):
            try:
                critique = self._critique(facts, proposal, eligible)
                break
            except ValueError:
                self.retry_count += 1
        if critique is None:
            raise ValueError(f"{case_id}: policy critic could not satisfy its JSON contract")
        trace.handoff(case_id, "policy_critic", "policy_finalizer", critique)
        correction = ""
        for _ in range(3):
            final_primary = self._finalize(facts, proposal, critique, eligible, correction)
            trace.handoff(case_id, "policy_finalizer", "coordinator_agent", {"primary_issue": final_primary})
            if final_primary in eligible:
                return resolver.resolve(final_primary, product, payment, delivery)
            self.retry_count += 1
            correction = f"{final_primary} is inconsistent with source facts"
        raise ValueError(f"{case_id}: finalizer could not choose a feasible policy issue")
