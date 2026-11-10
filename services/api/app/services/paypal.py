from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings, get_settings


class PayPalError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, ambiguous: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.ambiguous = ambiguous


def webhook_invoice_id(payload: dict[str, Any]) -> str | None:
    resource = payload.get("resource") or {}
    if not isinstance(resource, dict):
        return None
    invoice = resource.get("invoice")
    invoice = invoice if isinstance(invoice, dict) else {}
    value = resource.get("id") or resource.get("invoice_id") or invoice.get("id") or invoice.get("invoice_id")
    return str(value) if value else None


class PayPalAdapter:
    """Small typed boundary for the PayPal Invoicing v2 operations used by V1."""

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.http = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def _require_credentials(self) -> None:
        if not self.settings.live_configured:
            raise PayPalError("PayPal REST credentials are not configured.")

    def _require_sandbox(self) -> None:
        if self.settings.paypal_mode.lower() != "sandbox":
            raise PayPalError("PayPal mutations are restricted to Sandbox in V1.")

    def access_token(self, force: bool = False) -> str:
        self._require_credentials()
        with self._token_lock:
            if not force and self._token and time.time() < self._token_expires_at - 30:
                return self._token
            try:
                response = self.http.post(
                    f"{self.settings.paypal_api_base}/v1/oauth2/token",
                    auth=(self.settings.paypal_client_id, self.settings.paypal_client_secret),
                    data={"grant_type": "client_credentials"},
                )
                response.raise_for_status()
                result = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise PayPalError("PayPal OAuth token request failed.") from exc
            self._token = str(result["access_token"])
            self._token_expires_at = time.time() + int(result.get("expires_in", 300))
            return self._token

    def _request(self, method: str, path: str, *, payload: dict | None = None, params: dict | None = None,
                 safe_read: bool = False, extra_headers: dict[str, str] | None = None) -> httpx.Response:
        attempts = 3 if safe_read else 1
        for attempt in range(attempts):
            token = self.access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                **(extra_headers or {}),
            }
            try:
                response = self.http.request(method, f"{self.settings.paypal_api_base}{path}", headers=headers, json=payload, params=params)
                if response.status_code == 401 and safe_read and attempt == 0:
                    self.access_token(force=True)
                    continue
                if safe_read and response.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = min(2.0, max(0.0, float(retry_after)))
                    except ValueError:
                        delay = 0.25 * (2 ** attempt)
                    time.sleep(delay)
                    continue
                if response.is_error:
                    detail = "PayPal rejected the request."
                    try:
                        err = response.json()
                        message = str(err.get("message") or "")
                        name = str(err.get("name") or "")
                        issues = [
                            str(item.get("issue") or item.get("field") or item.get("name"))
                            for item in (err.get("details") or [])
                            if item.get("issue") or item.get("field") or item.get("name")
                        ]
                        detail = ": ".join(part for part in (name, ", ".join(issues), message) if part) or detail
                    except ValueError:
                        pass
                    raise PayPalError(detail, status_code=response.status_code,
                                      ambiguous=not safe_read and response.status_code >= 500)
                return response
            except httpx.TimeoutException as exc:
                if safe_read and attempt < attempts - 1:
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                raise PayPalError("PayPal request timed out.", ambiguous=not safe_read) from exc
            except httpx.TransportError as exc:
                if safe_read and attempt < attempts - 1:
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                raise PayPalError("PayPal connection failed.", ambiguous=not safe_read) from exc
        raise PayPalError("PayPal request failed after bounded read retries.")

    def list_invoices(self, page_size: int = 100) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while page <= 1000:
            response = self._request("GET", "/v2/invoicing/invoices", params={"page": page, "page_size": min(page_size, 100), "total_required": "true"}, safe_read=True)
            payload = response.json()
            items.extend(payload.get("items", []))
            if page >= int(payload.get("total_pages", page)):
                break
            page += 1
        return items

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/v2/invoicing/invoices/{invoice_id}", safe_read=True)
        return response.json()

    def remind_invoice(self, invoice_id: str) -> str:
        # PayPal controls the body of its native reminder email; the approved editable copy is retained in our audit record.
        self._require_sandbox()
        self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/remind", payload={"send_to_recipient": True})
        return invoice_id

    def create_invoice(
        self, recipient_email: str, amount: Decimal, currency: str, invoice_number: str,
        due_date: date, note: str, *, payment_term_type: str = "DUE_ON_DATE_SPECIFIED",
        partial_payment_allowed: bool = False, issue_date: date | None = None, request_id: str | None = None,
    ) -> dict[str, Any]:
        email = recipient_email.strip().lower()
        self._require_sandbox()
        if email not in self.settings.allowed_recipients:
            raise PayPalError("Recipient is not in PAYPAL_ALLOWED_RECIPIENT_EMAILS.")
        if amount <= 0:
            raise PayPalError("Invoice amount must be greater than zero.")
        currency = currency.upper()
        issue_date = issue_date or date.today()
        supported_terms = {"DUE_ON_RECEIPT", "NET_15", "NET_30", "DUE_ON_DATE_SPECIFIED"}
        if payment_term_type not in supported_terms:
            raise PayPalError("Unsupported payment term.")
        term = {"term_type": payment_term_type}
        if payment_term_type == "DUE_ON_DATE_SPECIFIED":
            term["due_date"] = due_date.isoformat()
        payload = {
            "detail": {"invoice_number": invoice_number, "invoice_date": issue_date.isoformat(), "currency_code": currency,
                       "payment_term": term, "note": note},
            "primary_recipients": [{"billing_info": {"email_address": email}}],
            "items": [{"name": "Sandbox receivables demonstration", "quantity": "1",
                       "unit_amount": {"currency_code": currency, "value": f"{amount:.2f}"}}],
        }
        if partial_payment_allowed:
            payload["configuration"] = {"partial_payment": {"allow_partial_payment": True}}
        response = self._request(
            "POST", "/v2/invoicing/invoices", payload=payload,
            extra_headers={"PayPal-Request-Id": request_id} if request_id else None,
        )
        try:
            created = response.json()
        except ValueError:
            created = {}
        if not isinstance(created, dict):
            created = {}
        if created.get("id"):
            return created

        # PayPal Sandbox can persist a draft while omitting its normal create response body.
        # Reconcile by its unique invoice number before reporting failure; never retry the
        # create request because that could produce duplicate invoices.
        try:
            lookup = self._request(
                "POST", "/v2/invoicing/search-invoices",
                params={"page": 1, "page_size": 10, "total_required": "true"},
                payload={"invoice_number": invoice_number, "recipient_email": email},
                safe_read=True,
            ).json()
        except (PayPalError, ValueError) as exc:
            raise PayPalError(
                "PayPal accepted the invoice but its ID could not be reconciled; check the Sandbox invoice list before retrying.",
                ambiguous=True,
            ) from exc

        matches = [
            invoice for invoice in lookup.get("items", [])
            if (invoice.get("detail") or {}).get("invoice_number") == invoice_number
            and invoice.get("id")
        ]
        if len(matches) == 1:
            return matches[0]
        raise PayPalError(
            "PayPal accepted the invoice but did not return one matching invoice ID; check the Sandbox invoice list before retrying.",
            ambiguous=True,
        )

    def send_invoice(self, invoice_id: str, request_id: str | None = None) -> str:
        self._require_sandbox()
        response = self._request(
            "POST", f"/v2/invoicing/invoices/{invoice_id}/send", payload={"send_to_recipient": True},
            extra_headers={"PayPal-Request-Id": request_id} if request_id else None,
        )
        return str(response.headers.get("PayPal-Debug-Id") or invoice_id)

    def enable_partial_payment(self, invoice_id: str) -> None:
        """Enable partial payments while preserving the current PayPal invoice fields."""
        self._require_sandbox()
        current = self.get_invoice(invoice_id)
        payload = dict(current)
        for read_only in ("id", "status", "amount", "due_amount", "payments", "links"):
            payload.pop(read_only, None)
        detail = dict(payload.get("detail") or {})
        detail.pop("metadata", None)
        payload["detail"] = detail
        configuration = dict(payload.get("configuration") or {})
        configuration["partial_payment"] = {"allow_partial_payment": True}
        payload["configuration"] = configuration
        self._request(
            "PUT", f"/v2/invoicing/invoices/{invoice_id}", payload=payload,
            params={"send_to_recipient": "true"},
        )

    def record_payment(self, invoice_id: str, amount: Decimal, currency: str, method: str = "BANK_TRANSFER", note: str = "") -> dict[str, Any]:
        self._require_sandbox()
        if amount <= 0:
            raise PayPalError("Payment amount must be greater than zero.")
        if len(currency) != 3 or not currency.isalpha():
            raise PayPalError("Currency must be a three-letter ISO 4217 code.")
        methods = {"BANK_TRANSFER", "CASH", "CHECK", "CREDIT_CARD", "DEBIT_CARD", "PAYPAL", "WIRE_TRANSFER", "OTHER"}
        method = method.upper()
        if method not in methods:
            raise PayPalError("Unsupported payment method.")
        response = self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/payments", payload={
            "method": method, "note": note[:2000], "payment_date": date.today().isoformat(),
            "amount": {"currency_code": currency.upper(), "value": format(amount, "f")},
        })
        return response.json() if response.content else {}

    def verify_webhook(self, headers: dict[str, str], event: dict) -> bool:
        if not self.settings.paypal_webhook_id:
            raise PayPalError("PAYPAL_WEBHOOK_ID is not configured.")
        required = ("paypal-transmission-id", "paypal-transmission-time", "paypal-cert-url", "paypal-auth-algo", "paypal-transmission-sig")
        normalized = {key.lower(): value for key, value in headers.items()}
        if any(not normalized.get(key) for key in required):
            return False
        body = {
            "transmission_id": normalized["paypal-transmission-id"],
            "transmission_time": normalized["paypal-transmission-time"],
            "cert_url": normalized["paypal-cert-url"],
            "auth_algo": normalized["paypal-auth-algo"],
            "transmission_sig": normalized["paypal-transmission-sig"],
            "webhook_id": self.settings.paypal_webhook_id,
            "webhook_event": event,
        }
        response = self._request("POST", "/v1/notifications/verify-webhook-signature", payload=body)
        return response.json().get("verification_status") == "SUCCESS"


def normalize_invoice(payload: dict[str, Any]) -> dict[str, Any]:
    detail = payload.get("detail") or {}
    amount = payload.get("amount") or {}
    due_amount = payload.get("due_amount") or {}
    status = str(payload.get("status", "SENT")).upper().replace(" ", "_")
    if status == "MARKED_AS_PAID":
        status = "PAID"
    if status in {"PARTIALLY_PAID", "PARTIAL_PAID"}:
        status = "PARTIALLY_PAID"
    total = Decimal(str(amount.get("value", "0")))
    outstanding = Decimal(str(due_amount.get("value", total if status != "PAID" else "0")))
    outstanding = min(total, max(Decimal("0"), outstanding))
    paid = max(Decimal("0"), total - outstanding)
    if status == "PAID":
        outstanding = Decimal("0.00")
        paid = total
    elif status in {"CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}:
        outstanding = Decimal("0.00")
        paid = Decimal("0.00")
    recipient = (payload.get("primary_recipients") or [{}])[0].get("billing_info") or {}
    name_parts = (recipient.get("name") or {})
    display_name = " ".join(str(name_parts.get(key, "")).strip() for key in ("given_name", "surname")).strip()
    if not display_name:
        display_name = recipient.get("business_name") or recipient.get("email_address") or "PayPal customer"
    issue_date = date.fromisoformat(detail.get("invoice_date") or (detail.get("metadata") or {}).get("create_time", date.today().isoformat())[:10])
    payment_term = detail.get("payment_term") or {}
    payment_term_type = str(payment_term.get("term_type") or "").upper() or None
    due_date_value = payment_term.get("due_date")
    if due_date_value:
        due_date = date.fromisoformat(due_date_value)
    elif payment_term_type and payment_term_type.startswith("NET_"):
        try:
            due_date = issue_date + timedelta(days=int(payment_term_type.removeprefix("NET_")))
        except ValueError:
            due_date = issue_date
    else:
        due_date = issue_date
    metadata = detail.get("metadata") or {}
    raw_viewed = detail.get("viewed_by_recipient", payload.get("viewed_by_recipient"))
    viewed = raw_viewed if isinstance(raw_viewed, bool) else None
    viewed_at = None
    for raw_time in (
        detail.get("recipient_viewed_time"), detail.get("viewed_at"), detail.get("view_time"),
        metadata.get("recipient_viewed_time"), metadata.get("viewed_at"), metadata.get("view_time"),
        payload.get("recipient_viewed_time"), payload.get("viewed_at"),
    ):
        if not raw_time:
            continue
        try:
            viewed_at = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            if viewed_at.tzinfo is None:
                viewed_at = viewed_at.replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    configuration = payload.get("configuration") or {}
    partial_payment = configuration.get("partial_payment") or {}
    return {
        "paypal_id": payload["id"], "invoice_number": str(detail.get("invoice_number") or payload["id"]),
        "client_identity": str(recipient.get("email_address") or payload["id"]).lower(), "client_name": display_name,
        "email": recipient.get("email_address"), "issue_date": issue_date, "due_date": due_date,
        "total": total, "paid": paid, "outstanding": outstanding,
        "currency": (amount.get("currency_code") or detail.get("currency_code") or "USD").upper(),
        "status": status, "payment_url": metadata.get("recipient_view_url"),
        "viewed_by_recipient": viewed, "viewed_at": viewed_at,
        "partial_payment_allowed": bool(partial_payment.get("allow_partial_payment")),
        "payment_term_type": payment_term_type,
        "payload": payload,
    }


def payment_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    payments = payload.get("payments") or {}
    if isinstance(payments, dict):
        payments = payments.get("transactions", payments.get("payments", []))
    records = []
    for item in payments if isinstance(payments, list) else []:
        pay_id = item.get("payment_id") or item.get("transaction_id") or item.get("id")
        amount = item.get("amount") or {}
        if not pay_id or not amount.get("value") or not amount.get("currency_code"):
            continue
        value = item.get("payment_date_time") or item.get("payment_date") or item.get("date")
        if not value:
            continue
        occurred = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        records.append({"provider_payment_id": str(pay_id), "amount": Decimal(str(amount["value"])),
                        "currency": str(amount["currency_code"]).upper(), "occurred_at": occurred})
    return records
