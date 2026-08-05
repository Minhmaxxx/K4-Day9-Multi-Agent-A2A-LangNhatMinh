"""Deterministic multi-agent investigation pipeline for EC_POLICY_V2.

The implementation deliberately uses the Python standard library only.  Every
agent returns a JSON-serialisable handoff, the coordinator applies policy, and
the verifier blocks invalid case files before they reach ``output/``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MODEL_NAME = "rule-based-local-deterministic"
MODEL_PARAMETER_SIZE = "0B"
POLICY_VERSION = "EC_POLICY_V2"
MONEY = Decimal("0.01")
TOLERANCE = Decimal("0.10")
MAXIMUMS = {
    "order_ids": 5,
    "item_ids": 5,
    "seller_ids": 3,
    "payment_ids": 5,
    "related_order_ids": 5,
    "product_ids": 5,
    "category_names": 5,
    "ranked_causes": 3,
    "responsible_parties": 3,
    "evidence_ids": 20,
    "resolution_actions": 5,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    # utf-8-sig safely strips a UTF-8 BOM when a supplied CSV has one.
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def decimal(value: str | None) -> Decimal:
    return Decimal(value or "0")


def money(value: Decimal) -> float:
    """Return BRL numbers rounded exactly as required by the exercise."""
    return float(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def timestamp(value: str | None) -> Optional[datetime]:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S") if value else None


def hours_between(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[float]:
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds() / 3600, 2)


def stable_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def cap(values: list[Any], name: str) -> list[Any]:
    return values[:MAXIMUMS[name]]


@dataclass
class DataStore:
    orders: dict[str, dict[str, str]]
    customers: dict[str, dict[str, str]]
    items_by_order: dict[str, list[dict[str, str]]]
    payments_by_order: dict[str, list[dict[str, str]]]
    products: dict[str, dict[str, str]]
    category_translations: dict[str, str]
    orders_by_customer: dict[str, list[str]]

    @classmethod
    def load(cls, data_dir: Path) -> "DataStore":
        orders_rows = read_csv(data_dir / "olist_orders_dataset.csv")
        customer_rows = read_csv(data_dir / "olist_customers_dataset.csv")
        item_rows = read_csv(data_dir / "olist_order_items_dataset.csv")
        payment_rows = read_csv(data_dir / "olist_order_payments_dataset.csv")
        product_rows = read_csv(data_dir / "olist_products_dataset.csv")
        translation_rows = read_csv(data_dir / "product_category_name_translation.csv")

        customers = {row["customer_id"]: row for row in customer_rows}
        orders = {row["order_id"]: row for row in orders_rows}
        items_by_order: dict[str, list[dict[str, str]]] = defaultdict(list)
        payments_by_order: dict[str, list[dict[str, str]]] = defaultdict(list)
        orders_by_customer: dict[str, list[str]] = defaultdict(list)
        for row in item_rows:
            items_by_order[row["order_id"]].append(row)
        for row in payment_rows:
            payments_by_order[row["order_id"]].append(row)
        for row in orders_rows:
            customer = customers.get(row["customer_id"])
            if customer:
                orders_by_customer[customer["customer_unique_id"]].append(row["order_id"])
        return cls(
            orders=orders,
            customers=customers,
            items_by_order=items_by_order,
            payments_by_order=payments_by_order,
            products={row["product_id"]: row for row in product_rows},
            category_translations={row["product_category_name"]: row["product_category_name_english"] for row in translation_rows},
            orders_by_customer=orders_by_customer,
        )


class Trace:
    def __init__(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination = destination
        self.records: list[dict[str, Any]] = []

    def handoff(self, case_id: str, sender: str, recipient: str, payload: dict[str, Any]) -> None:
        self.records.append({
            "case_id": case_id,
            "event": "handoff",
            "sender": sender,
            "recipient": recipient,
            "payload_keys": sorted(payload.keys()),
        })

    def result(self, case_id: str, valid: bool) -> None:
        self.records.append({"case_id": case_id, "event": "verification", "valid": valid})

    def flush(self) -> None:
        with self.destination.open("w", encoding="utf-8", newline="\n") as target:
            for record in self.records:
                target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


class CustomerAgent:
    name = "customer_agent"

    def investigate(self, order: dict[str, str], scope: dict[str, Any], data: DataStore) -> dict[str, Any]:
        customer = data.customers[order["customer_id"]]
        unique_id = customer["customer_unique_id"]
        related = []
        if scope.get("include_customer_history", False):
            related = [order_id for order_id in data.orders_by_customer[unique_id] if order_id != order["order_id"]]
        return {
            "customer_unique_id": unique_id,
            "related_order_ids": cap(related, "related_order_ids"),
        }


class OrderProductAgent:
    name = "order_product_agent"

    def __init__(self, category_language: str = "source") -> None:
        self.category_language = category_language

    def investigate(self, order: dict[str, str], scope: dict[str, Any], data: DataStore) -> dict[str, Any]:
        items = data.items_by_order.get(order["order_id"], [])
        seller_ids = stable_unique(item["seller_id"] for item in items)
        product_ids: list[str] = []
        categories: list[str] = []
        if scope.get("include_product_context", False):
            product_ids = stable_unique(item["product_id"] for item in items)
            source_categories = [data.products.get(product_id, {}).get("product_category_name", "") for product_id in product_ids]
            if self.category_language == "english":
                categories = stable_unique(data.category_translations.get(category, category) for category in source_categories)
            else:
                categories = stable_unique(source_categories)
        return {
            "items": items,
            "item_ids": cap([f"{order['order_id']}:{item['order_item_id']}" for item in items], "item_ids"),
            "seller_ids": cap(seller_ids, "seller_ids"),
            "product_ids": cap(product_ids, "product_ids"),
            "category_names": cap(categories, "category_names"),
        }


class PaymentAgent:
    name = "payment_agent"

    def investigate(self, order_id: str, items: list[dict[str, str]], data: DataStore) -> dict[str, Any]:
        payments = data.payments_by_order.get(order_id, [])
        payment_total = sum((decimal(row["payment_value"]) for row in payments), Decimal("0"))
        if not items:
            item_total = freight_total = expected = difference = reconciled = None
        else:
            item_total_decimal = sum((decimal(row["price"]) for row in items), Decimal("0"))
            freight_total_decimal = sum((decimal(row["freight_value"]) for row in items), Decimal("0"))
            expected_decimal = item_total_decimal + freight_total_decimal
            difference_decimal = payment_total - expected_decimal
            item_total = money(item_total_decimal)
            freight_total = money(freight_total_decimal)
            expected = money(expected_decimal)
            difference = money(difference_decimal)
            reconciled = abs(difference_decimal) <= TOLERANCE
        return {
            "payments": payments,
            "payment_ids": cap([f"{order_id}:{row['payment_sequential']}" for row in payments], "payment_ids"),
            "payment_count": len(payments),
            "currency": "BRL",
            "item_total_brl": item_total,
            "freight_total_brl": freight_total,
            "expected_total_brl": expected,
            "payment_total_brl": money(payment_total),
            "difference_brl": difference,
            "reconciled": reconciled,
            "payment_types": stable_unique(row["payment_type"] for row in payments),
            "payment_total_decimal": payment_total,
            "freight_total_decimal": None if not items else freight_total_decimal,
        }


class DeliveryAgent:
    name = "delivery_agent"

    def investigate(self, order: dict[str, str], items: list[dict[str, str]]) -> dict[str, Any]:
        delivered_at = order["order_delivered_customer_date"] or None
        estimated_at = order["order_estimated_delivery_date"] or None
        carrier_at = order["order_delivered_carrier_date"] or None
        delivered = timestamp(delivered_at)
        estimated = timestamp(estimated_at)
        carrier = timestamp(carrier_at)
        seller_deadlines: dict[str, list[str]] = defaultdict(list)
        seller_order: list[str] = []
        for item in items:
            seller_id = item["seller_id"]
            if seller_id not in seller_deadlines:
                seller_order.append(seller_id)
            if item["shipping_limit_date"]:
                seller_deadlines[seller_id].append(item["shipping_limit_date"])

        analyses: list[dict[str, Any]] = []
        late_sellers: list[str] = []
        for seller_id in seller_order:
            earliest = min(seller_deadlines[seller_id]) if seller_deadlines[seller_id] else None
            variance = hours_between(carrier, timestamp(earliest)) if earliest else None
            late = variance is not None and variance > 0
            if late:
                late_sellers.append(seller_id)
            analyses.append({
                "seller_id": seller_id,
                "shipping_limit_at": earliest,
                "handoff_variance_hours": variance,
                "late_handoff": late,
            })
        return {
            "delivered_at": delivered_at,
            "estimated_delivery_at": estimated_at,
            "carrier_handoff_at": carrier_at,
            "delivery_variance_hours": hours_between(delivered, estimated),
            "seller_handoff_analysis": analyses,
            "late_handoff_seller_ids": cap(late_sellers, "seller_ids"),
            "delivery_late": delivered is not None and estimated is not None and delivered > estimated,
        }


class PolicyAgent:
    name = "policy_agent"

    def decide(
        self,
        order: dict[str, str],
        product: dict[str, Any],
        payment: dict[str, Any],
        delivery: dict[str, Any],
        primary_override: str | None = None,
    ) -> dict[str, Any]:
        if primary_override is None:
            status = order["order_status"]
            paid = payment["payment_total_decimal"] > 0
            if status == "canceled" and paid:
                primary = "canceled_order_paid"
            elif status == "unavailable" and paid:
                primary = "unavailable_order_paid"
            elif delivery["delivery_late"] and delivery["late_handoff_seller_ids"]:
                primary = "late_delivery_seller"
            elif delivery["delivery_late"]:
                primary = "late_delivery_logistics"
            elif payment["payment_count"] >= 2 and payment["reconciled"] is True:
                primary = "valid_split_payment"
            else:
                primary = "unsupported_late_claim"
        else:
            primary = primary_override
        return self.resolve(primary, product, payment, delivery)

    def resolve(
        self, primary: str, product: dict[str, Any], payment: dict[str, Any], delivery: dict[str, Any]
    ) -> dict[str, Any]:
        cause: str
        parties: list[dict[str, str]]
        refund = Decimal("0")
        actions: list[str]
        if primary == "canceled_order_paid":
            primary, cause = "canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT"
            parties, refund, actions = ([{"party_type": "platform", "party_id": "OLIST_PLATFORM"}], payment["payment_total_decimal"], ["issue_full_refund"])
        elif primary == "unavailable_order_paid":
            primary, cause = "unavailable_order_paid", "ORDER_UNAVAILABLE_AFTER_PAYMENT"
            parties, refund, actions = ([{"party_type": "platform", "party_id": "OLIST_PLATFORM"}], payment["payment_total_decimal"], ["issue_full_refund"])
        elif primary == "late_delivery_seller":
            primary, cause = "late_delivery_seller", "SELLER_HANDOFF_AFTER_LIMIT"
            parties = [{"party_type": "seller", "party_id": seller} for seller in delivery["late_handoff_seller_ids"]]
            refund, actions = payment["freight_total_decimal"] or Decimal("0"), ["refund_freight"]
        elif primary == "late_delivery_logistics":
            primary, cause = "late_delivery_logistics", "CARRIER_DELIVERED_AFTER_ESTIMATE"
            parties, refund, actions = ([{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}], payment["freight_total_decimal"] or Decimal("0"), ["refund_freight"])
        elif primary == "valid_split_payment":
            primary, cause = "valid_split_payment", "MULTIPLE_PAYMENTS_RECONCILED"
            parties, actions = [], ["explain_valid_split_payment"]
        elif primary == "unsupported_late_claim":
            primary, cause = "unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE"
            parties, actions = [], ["reject_late_refund"]
        else:
            raise ValueError(f"Unknown primary issue: {primary}")

        secondary: list[str] = []
        if len(product["items"]) >= 2:
            secondary.append("multi_item_order")
        if len(product["seller_ids"]) >= 2:
            secondary.append("multi_seller_order")
        if payment["payment_count"] >= 2:
            secondary.append("split_payment")
        # The coordinator adds repeat_customer from its customer handoff.
        if len(product["category_names"]) >= 2:
            secondary.append("multiple_categories")

        if primary == "late_delivery_seller":
            actions.append("review_seller_handoff")
        elif primary == "late_delivery_logistics":
            actions.append("review_carrier_delay")
        elif primary in {"canceled_order_paid", "unavailable_order_paid"}:
            actions.append("verify_refund_completion")
        if "multi_seller_order" in secondary:
            actions.append("coordinate_multi_seller_case")
        if "split_payment" in secondary and primary != "valid_split_payment":
            actions.append("verify_payment_allocation")
        return {
            "primary_issue": primary,
            "secondary_issues": secondary,
            "cause_code": cause,
            "responsible_parties": cap(parties, "responsible_parties"),
            "recommended_refund_brl": money(refund),
            "resolution_actions": cap(actions, "resolution_actions"),
        }


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE settings without adding a python-dotenv dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def extract_json_object(content: str) -> dict[str, Any]:
    """Extract the final JSON object when a chat model adds prose or a think block."""
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in reversed(candidates):
        if "primary_issue" in candidate:
            return candidate
    raise ValueError("LLM did not return a JSON object containing primary_issue")


class LLMPolicyAgent:
    """A constrained policy agent served by a permitted local LM Studio model.

    It receives normalised handoffs and selects the primary policy issue. The
    public policy resolver then derives amounts, parties, and actions from that
    selected issue and the verified source facts. This keeps numeric work out
    of a small local model without encoding any case-specific answer.
    """

    name = "llm_policy_agent"
    primary_to_cause = {
        "canceled_order_paid": "ORDER_CANCELED_AFTER_PAYMENT",
        "unavailable_order_paid": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
        "late_delivery_seller": "SELLER_HANDOFF_AFTER_LIMIT",
        "late_delivery_logistics": "CARRIER_DELIVERED_AFTER_ESTIMATE",
        "valid_split_payment": "MULTIPLE_PAYMENTS_RECONCILED",
        "unsupported_late_claim": "DELIVERY_WITHIN_ESTIMATE",
    }
    def __init__(self) -> None:
        load_dotenv()
        self.base_url = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
        self.model = os.getenv("LMSTUDIO_MODEL", "")
        self.token = os.getenv("LMSTUDIO_API_TOKEN", "")
        if not self.model:
            raise ValueError("LLM mode requires LMSTUDIO_MODEL in .env")
        self.retry_count = 0

    def _chat(self, facts: dict[str, Any], correction: str = "") -> dict[str, Any]:
        prompt = """You are the Policy Agent for EC_POLICY_V2. Return JSON only; no markdown or explanation.
Apply these rules in exact priority: (1) canceled and payment_total>0 -> canceled_order_paid;
(2) unavailable and payment_total>0 -> unavailable_order_paid; (3) delivery_late and nonempty
late_handoff_seller_ids -> late_delivery_seller; (4) delivery_late -> late_delivery_logistics;
(5) payment_count>=2 and reconciled=true -> valid_split_payment; otherwise unsupported_late_claim.
Return exactly {"primary_issue":"one_allowed_value"}. The allowed values are:
canceled_order_paid, unavailable_order_paid, late_delivery_seller, late_delivery_logistics,
valid_split_payment, unsupported_late_claim.
Facts:\n""" + json.dumps(facts, ensure_ascii=False, separators=(",", ":")) + "\n/no_think"
        if correction:
            prompt += "\nThe previous answer was rejected: " + correction + ". Return a corrected JSON object only."
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "max_tokens": 80,
            "messages": [
                {"role": "system", "content": "You output only strict JSON for a deterministic policy contract."},
                {"role": "user", "content": prompt},
            ],
        }).encode("utf-8")
        request = Request(f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as error:
            raise ValueError(f"LM Studio request failed: {error}") from error
        return extract_json_object(payload["choices"][0]["message"]["content"])

    def _validate(
        self,
        candidate: dict[str, Any],
    ) -> str:
        if set(candidate) != {"primary_issue"}:
            raise ValueError("LLM policy JSON must contain only primary_issue")
        primary = candidate["primary_issue"]
        if primary not in self.primary_to_cause:
            raise ValueError("LLM returned an unsupported primary_issue")
        return primary

    def decide(self, order: dict[str, str], product: dict[str, Any], payment: dict[str, Any], delivery: dict[str, Any]) -> dict[str, Any]:
        facts = {
            "order_status": order["order_status"],
            "payment_total": payment["payment_total_brl"],
            "payment_count": payment["payment_count"],
            "reconciled": payment["reconciled"],
            "freight_total": payment["freight_total_brl"],
            "item_count": len(product["items"]),
            "seller_ids": product["seller_ids"],
            "category_count": len(product["category_names"]),
            "delivery_late": delivery["delivery_late"],
            "late_handoff_seller_ids": delivery["late_handoff_seller_ids"],
        }
        correction = ""
        for _ in range(3):
            try:
                primary = self._validate(self._chat(facts, correction))
                return PolicyAgent().decide(order, product, payment, delivery, primary_override=primary)
            except (KeyError, TypeError, ValueError) as error:
                self.retry_count += 1
                correction = str(error)
        raise ValueError("LLM policy could not satisfy the response contract after 3 local attempts")


class VerifierAgent:
    name = "verifier_agent"

    def verify(self, result: dict[str, Any], case: dict[str, Any], data: DataStore) -> None:
        order_id = case["customer_request"]["claimed_order_id"]
        if result["case_id"] != case["case_id"] or result["affected_entities"]["order_ids"] != [order_id]:
            raise ValueError(f"{case['case_id']}: case or claimed order mismatch")
        if result["case_assessment"]["case_status"] not in {"action_required", "no_action"}:
            raise ValueError(f"{case['case_id']}: invalid case status")
        confidence = result["case_assessment"]["confidence"]
        if not isinstance(confidence, (float, int)) or not 0 <= confidence <= 1:
            raise ValueError(f"{case['case_id']}: invalid confidence")
        for section, key in (("affected_entities", "order_ids"), ("affected_entities", "item_ids"),
                             ("affected_entities", "seller_ids"), ("affected_entities", "payment_ids"),
                             ("customer_context", "related_order_ids"), ("product_context", "product_ids"),
                             ("product_context", "category_names"), ("root_cause_analysis", "ranked_causes"),
                             ("root_cause_analysis", "responsible_parties"), ("", "evidence_ids"), ("", "resolution_actions")):
            values = result[key] if not section else result[section][key]
            if len(values) > MAXIMUMS[key]:
                raise ValueError(f"{case['case_id']}: {key} exceeds limit")
        if any(item_id.split(":")[0] != order_id for item_id in result["affected_entities"]["item_ids"]):
            raise ValueError(f"{case['case_id']}: invalid item id")
        if any(payment_id.split(":")[0] != order_id for payment_id in result["affected_entities"]["payment_ids"]):
            raise ValueError(f"{case['case_id']}: invalid payment id")
        items = data.items_by_order.get(order_id, [])
        payments = data.payments_by_order.get(order_id, [])
        valid_item_ids = {f"{order_id}:{item['order_item_id']}" for item in items}
        valid_payment_ids = {f"{order_id}:{payment['payment_sequential']}" for payment in payments}
        valid_seller_ids = {item["seller_id"] for item in items}
        if not set(result["affected_entities"]["item_ids"]).issubset(valid_item_ids):
            raise ValueError(f"{case['case_id']}: affected item is not in source data")
        if not set(result["affected_entities"]["payment_ids"]).issubset(valid_payment_ids):
            raise ValueError(f"{case['case_id']}: affected payment is not in source data")
        if not set(result["affected_entities"]["seller_ids"]).issubset(valid_seller_ids):
            raise ValueError(f"{case['case_id']}: affected seller is not in source data")
        for party in result["root_cause_analysis"]["responsible_parties"]:
            if party["party_type"] == "seller" and party["party_id"] not in valid_seller_ids:
                raise ValueError(f"{case['case_id']}: responsible seller is not in source data")
        if result["case_assessment"]["case_status"] != (
            "action_required" if result["financial_resolution"]["recommended_refund_brl"] > 0 else "no_action"
        ):
            raise ValueError(f"{case['case_id']}: refund and case status disagree")
        reconciliation = result["payment_reconciliation"]
        if not items and any(reconciliation[key] is not None for key in (
            "item_total_brl", "freight_total_brl", "expected_total_brl", "difference_brl", "reconciled"
        )):
            raise ValueError(f"{case['case_id']}: no-item reconciliation must use nulls")
        if not items and result["delivery_analysis"]["seller_handoff_analysis"]:
            raise ValueError(f"{case['case_id']}: no-item order cannot have seller handoff analysis")
        if len(result["evidence_ids"]) != len(set(result["evidence_ids"])):
            raise ValueError(f"{case['case_id']}: duplicate evidence")
        if not all(evidence.startswith(("order:", "item:", "payment:", "seller:", "policy:")) for evidence in result["evidence_ids"]):
            raise ValueError(f"{case['case_id']}: evidence format")
        valid_causes = {
            "SELLER_HANDOFF_AFTER_LIMIT", "CARRIER_DELIVERED_AFTER_ESTIMATE",
            "ORDER_CANCELED_AFTER_PAYMENT", "ORDER_UNAVAILABLE_AFTER_PAYMENT",
            "MULTIPLE_PAYMENTS_RECONCILED", "DELIVERY_WITHIN_ESTIMATE",
        }
        for evidence in result["evidence_ids"]:
            prefix, value = evidence.split(":", 1)
            if prefix == "order" and value != order_id:
                raise ValueError(f"{case['case_id']}: unsupported order evidence")
            if prefix == "item" and value not in valid_item_ids:
                raise ValueError(f"{case['case_id']}: unsupported item evidence")
            if prefix == "payment" and value not in valid_payment_ids:
                raise ValueError(f"{case['case_id']}: unsupported payment evidence")
            if prefix == "seller" and value not in valid_seller_ids:
                raise ValueError(f"{case['case_id']}: unsupported seller evidence")
            if prefix == "policy" and value not in valid_causes:
                raise ValueError(f"{case['case_id']}: unsupported policy evidence")


class CoordinatorAgent:
    name = "coordinator_agent"

    def __init__(
        self, data: DataStore, trace: Trace, policy_agent: Any | None = None, category_language: str = "source"
    ) -> None:
        self.data, self.trace = data, trace
        self.customer_agent = CustomerAgent()
        self.order_product_agent = OrderProductAgent(category_language)
        self.payment_agent = PaymentAgent()
        self.delivery_agent = DeliveryAgent()
        self.policy_agent = policy_agent or PolicyAgent()
        self.verifier_agent = VerifierAgent()

    def _handoff(self, case_id: str, agent: Any, method: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        payload = method(*args)
        self.trace.handoff(case_id, agent.name, self.name, payload)
        return payload

    def investigate(self, case: dict[str, Any]) -> dict[str, Any]:
        if case.get("policy_version") != POLICY_VERSION:
            raise ValueError(f"{case.get('case_id', '<unknown>')}: unsupported policy version")
        case_id = case["case_id"]
        order_id = case["customer_request"]["claimed_order_id"]
        order = self.data.orders.get(order_id)
        if not order:
            raise ValueError(f"{case_id}: claimed_order_id does not exist")
        scope = case.get("investigation_scope", {})
        customer = self._handoff(case_id, self.customer_agent, self.customer_agent.investigate, order, scope, self.data)
        product = self._handoff(case_id, self.order_product_agent, self.order_product_agent.investigate, order, scope, self.data)
        payment = self._handoff(case_id, self.payment_agent, self.payment_agent.investigate, order_id, product["items"], self.data)
        delivery = self._handoff(case_id, self.delivery_agent, self.delivery_agent.investigate, order, product["items"])
        policy = self._handoff(case_id, self.policy_agent, self.policy_agent.decide, order, product, payment, delivery)
        if customer["related_order_ids"]:
            policy["secondary_issues"].insert(3, "repeat_customer")

        responsible_sellers = [party["party_id"] for party in policy["responsible_parties"] if party["party_type"] == "seller"]
        evidence = [f"order:{order_id}"]
        evidence.extend(f"item:{item_id}" for item_id in product["item_ids"])
        evidence.extend(f"payment:{payment_id}" for payment_id in payment["payment_ids"])
        evidence.extend(f"seller:{seller_id}" for seller_id in responsible_sellers)
        evidence.append(f"policy:{policy['cause_code']}")
        refund = policy["recommended_refund_brl"]
        result = {
            "case_id": case_id,
            "case_assessment": {
                "primary_issue": policy["primary_issue"],
                "secondary_issues": policy["secondary_issues"],
                "case_status": "action_required" if refund > 0 else "no_action",
                "confidence": 0.98 if refund > 0 else 0.95,
            },
            "affected_entities": {
                "order_ids": [order_id],
                "item_ids": product["item_ids"],
                "seller_ids": product["seller_ids"],
                "payment_ids": payment["payment_ids"],
            },
            "customer_context": customer,
            "product_context": {"product_ids": product["product_ids"], "category_names": product["category_names"]},
            "delivery_analysis": {
                key: delivery[key]
                for key in ("delivered_at", "estimated_delivery_at", "carrier_handoff_at", "delivery_variance_hours",
                            "seller_handoff_analysis", "late_handoff_seller_ids")
            },
            "payment_reconciliation": {
                key: payment[key]
                for key in ("currency", "item_total_brl", "freight_total_brl", "expected_total_brl", "payment_total_brl",
                            "difference_brl", "reconciled", "payment_types")
            },
            "root_cause_analysis": {
                "ranked_causes": [{"cause_code": policy["cause_code"], "rank": 1}],
                "responsible_parties": policy["responsible_parties"],
            },
            "evidence_ids": cap(evidence, "evidence_ids"),
            "financial_resolution": {"currency": "BRL", "recommended_refund_brl": refund},
            "resolution_actions": policy["resolution_actions"],
        }
        self.verifier_agent.verify(result, case, self.data)
        self.trace.result(case_id, True)
        return result


def run_pipeline(
    input_dir: Path,
    data_dir: Path,
    output_dir: Path,
    trace_path: Path,
    metadata_path: Path,
    policy_mode: str = "rules",
    llm_parameter_size: str = "4B",
    category_language: str = "source",
) -> int:
    case_paths = sorted(input_dir.glob("EC_*.json"))
    if len(case_paths) != 50:
        raise ValueError(f"Expected exactly 50 input cases (EC_001.json..EC_050.json), found {len(case_paths)} in {input_dir}")
    expected_names = {f"EC_{number:03d}.json" for number in range(1, 51)}
    if {path.name for path in case_paths} != expected_names:
        raise ValueError("Input filenames must be exactly EC_001.json through EC_050.json")

    data = DataStore.load(data_dir)
    trace = Trace(trace_path)
    if policy_mode == "rules":
        policy_agent: Any = PolicyAgent()
    elif policy_mode == "llm":
        policy_agent = LLMPolicyAgent()
    else:
        raise ValueError(f"Unsupported policy mode: {policy_mode}")
    coordinator = CoordinatorAgent(data, trace, policy_agent, category_language)
    staging = output_dir.with_name(f"{output_dir.name}_staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        for case_path in case_paths:
            with case_path.open("r", encoding="utf-8") as source:
                result = coordinator.investigate(json.load(source))
            with (staging / case_path.name).open("w", encoding="utf-8", newline="\n") as target:
                json.dump(result, target, ensure_ascii=False, indent=2)
                target.write("\n")
        if output_dir.exists():
            for path in output_dir.glob("EC_*.json"):
                path.unlink()
        else:
            output_dir.mkdir(parents=True)
        for source in staging.glob("EC_*.json"):
            source.replace(output_dir / source.name)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    trace.flush()
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "model": policy_agent.model if policy_mode == "llm" else MODEL_NAME,
        "parameter_size": llm_parameter_size if policy_mode == "llm" else MODEL_PARAMETER_SIZE,
        "framework": (
            "Python 3 standard library; Qwen policy agent through local LM Studio; deterministic data agents and verifier"
            if policy_mode == "llm" else "Python 3 standard library; deterministic A2A-style handoffs"
        ),
        "runtime": "local",
        "policy_version": POLICY_VERSION,
        "policy_mode": policy_mode,
        "category_language": category_language,
        "cases_processed": len(case_paths),
    }
    if policy_mode == "llm":
        metadata["llm_retry_count"] = policy_agent.retry_count
    with metadata_path.open("w", encoding="utf-8", newline="\n") as target:
        json.dump(metadata, target, ensure_ascii=False, indent=2)
        target.write("\n")
    return len(case_paths)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the EC_POLICY_V2 multi-agent pipeline.")
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--trace", type=Path, default=Path("logging/trace.jsonl"))
    parser.add_argument("--metadata", type=Path, default=Path("logging/metadata.json"))
    parser.add_argument("--policy-mode", choices=("rules", "llm"), default="rules")
    parser.add_argument("--llm-parameter-size", default="4B")
    parser.add_argument("--category-language", choices=("source", "english"), default="source")
    arguments = parser.parse_args()
    count = run_pipeline(
        arguments.input_dir,
        arguments.data_dir,
        arguments.output_dir,
        arguments.trace,
        arguments.metadata,
        arguments.policy_mode,
        arguments.llm_parameter_size,
        arguments.category_language,
    )
    print(f"Validated and wrote {count} case outputs to {arguments.output_dir}")


if __name__ == "__main__":
    main()
