"""
Minimal Vault SOAP client — used by the release workflow for lifecycle changes.

Why this exists
---------------
The Vault Data API (REST v2) is read-only for lifecycle: there is no
endpoint to advance a file or item to the next state. The legacy SOAP
services (still shipped on every Vault server) DO expose those operations,
so the release workflow falls back to SOAP for the final two steps:

    * `update_file_lifecycle_states`  → DocumentServiceExtensions
    * `update_item_lifecycle_states`  → ItemService

We avoid taking on `zeep` as a dependency: only two operations are needed
and the request envelopes are small enough to template by hand.

Authentication
--------------
The REST sign-in returns an `accessToken` of the form ``"V:<base64-json>"``.
The base64 payload decodes to JSON like ``{"Ticket": "...", "UserId": 2}``.
Vault SOAP requires those two values in a ``SecurityHeader`` SOAP header
on every call.

Endpoints
---------
The standard Filestore SOAP services live at:

    {servername}/AutodeskDM/Services/Filestore/v26/{ServiceName}.asmx

The v26 version corresponds to Vault 2025; older Vault releases use lower
numbers. The release workflow defaults to v26 but the version is wired
through as a parameter so it's easy to override.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Iterable, Sequence

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def extract_ticket(access_token: str) -> str:
    """Strip the ``V:`` prefix from a Vault REST access token.

    The REST ``accessToken`` is formatted as ``V:<ticket-guid>`` — the
    ticket GUID is what every SOAP call needs in its ``SecurityHeader``.
    """
    if not access_token:
        raise ValueError("empty access token")
    return access_token[2:] if access_token.startswith("V:") else access_token


# Back-compat alias — old code may still import this name.
def decode_access_token(access_token: str) -> tuple[str, str]:  # noqa: D401
    """Deprecated: use ``extract_ticket`` and pass ``user_id`` explicitly."""
    return extract_ticket(access_token), ""


# ---------------------------------------------------------------------------
# SOAP envelope plumbing
# ---------------------------------------------------------------------------

_SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
_VAULT_NS = "http://AutodeskDM/Services"
_FILESTORE_NS = "http://AutodeskDM/Services/Filestore"
_CONNECTIVITY_NS = "http://AutodeskDM/Services/Connectivity"

# Vault organises its SOAP services into a few "categories" — each lives at a
# different URL prefix and uses a different XML namespace. Document /
# DocumentServiceExtensions / ItemService are under Filestore (they touch
# stored content); LifeCycleService / CategoryService / PropertyService are
# under Connectivity (they touch metadata definitions).
SERVICE_CATEGORIES: dict[str, dict[str, str]] = {
    "Filestore":    {"path": "Filestore",    "ns": _FILESTORE_NS},
    "Connectivity": {"path": "Connectivity", "ns": _CONNECTIVITY_NS},
}

_ENVELOPE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope
    xmlns:soap="{soap_ns}"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Header>
    <SecurityHeader xmlns="{vault_ns}">
      <Ticket>{ticket}</Ticket>
      <UserId>{user_id}</UserId>
    </SecurityHeader>
  </soap:Header>
  <soap:Body>
    {body}
  </soap:Body>
</soap:Envelope>"""


def _build_envelope(ticket: str, user_id: str, body_xml: str) -> str:
    return _ENVELOPE.format(
        soap_ns=_SOAP_NS,
        vault_ns=_VAULT_NS,
        ticket=_escape(ticket),
        user_id=_escape(user_id),
        body=body_xml,
    )


def _escape(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _ints_xml(tag: str, values: Iterable[int | str]) -> str:
    return "".join(f"<{tag}>{int(v)}</{tag}>" for v in values)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class VaultSoapError(RuntimeError):
    """Raised for any SOAP fault or transport-level failure."""


class VaultSoapClient:
    """Tiny Vault SOAP client. Synchronous (httpx) — release calls are rare."""

    def __init__(
        self,
        servername: str,
        access_token: str,
        user_id: str | int,
        *,
        version: str = "v26",
        verify_tls: bool = False,
        timeout: float = 60.0,
    ):
        """Construct a SOAP client.

        ``access_token`` is the REST ``V:<guid>`` token; ``user_id`` is the
        Vault user id (returned alongside the access token by sign-in —
        ``data.userInformation.id``). Both must be present in the
        ``SecurityHeader`` of every SOAP call.
        """
        # Per-category base URL — built lazily from servername + version so
        # we can post to either Filestore or Connectivity from one client.
        self.servername = servername.rstrip("/")
        self.version = version
        self.ticket = extract_ticket(access_token)
        self.user_id = str(user_id)
        if not self.user_id:
            raise ValueError("user_id is required (extract from sign-in response)")
        self.verify_tls = verify_tls
        self.timeout = timeout

    # ----- Transport ----------------------------------------------------

    def _post(
        self, service: str, soap_action: str, body_xml: str,
        *, category: str = "Filestore",
    ) -> str:
        cat = SERVICE_CATEGORIES.get(category)
        if cat is None:
            raise ValueError(f"unknown service category: {category!r}")
        url = (
            f"{self.servername}/AutodeskDM/Services/{cat['path']}/"
            f"{self.version}/{service}.asmx"
        )
        envelope = _build_envelope(self.ticket, self.user_id, body_xml)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
        }
        logger.info("SOAP POST %s  action=%s", url, soap_action)
        try:
            with httpx.Client(verify=self.verify_tls, timeout=self.timeout) as client:
                resp = client.post(url, headers=headers, content=envelope.encode("utf-8"))
        except httpx.HTTPError as exc:
            raise VaultSoapError(f"SOAP transport error: {exc}") from exc

        text = resp.text
        if resp.status_code >= 400:
            # SOAP faults still come back with status 500; extract <faultstring>
            fault = _extract_tag(text, "faultstring") or text[:500]
            raise VaultSoapError(
                f"SOAP {soap_action} failed (HTTP {resp.status_code}): {fault}"
            )
        return text

    # ----- DocumentServiceExtensions: file lifecycle --------------------

    def update_file_lifecycle_states(
        self,
        file_master_ids: Sequence[int | str],
        to_state_id: int | str,
        comment: str = "Released via release workflow",
    ) -> str:
        """Promote one or more files to the given lifecycle state.

        Vault expects parallel arrays — every file gets the same target
        state. Returns the raw SOAP response body for the caller to log.
        Raises ``VaultSoapError`` on fault.
        """
        if not file_master_ids:
            raise ValueError("file_master_ids must not be empty")

        files_xml = _ints_xml("long", file_master_ids)
        states_xml = _ints_xml("long", [to_state_id] * len(list(file_master_ids)))

        body = f"""
          <UpdateFileLifeCycleStates xmlns="{_FILESTORE_NS}">
            <fileMasterIds>{files_xml}</fileMasterIds>
            <toStateIds>{states_xml}</toStateIds>
            <comment>{_escape(comment)}</comment>
          </UpdateFileLifeCycleStates>
        """
        return self._post(
            "DocumentServiceExtensions",
            f"{_FILESTORE_NS}/UpdateFileLifeCycleStates",
            body,
        )

    # ----- LifeCycleService: enumerate definitions / states -------------

    def get_all_lifecycle_definitions(self) -> str:
        """Return the SOAP response body for `GetAllLifeCycleDefinitions`.

        Used by the workflow GUI to populate the Target State dropdown
        with the full set of lifecycle states defined on the server (not
        just the ones currently in use on items). Returns raw XML — pair
        with ``parse_lifecycle_state_names`` to get a flat list of names.

        Lives under the Connectivity service category, NOT Filestore (the
        latter is for content services like DocumentService / ItemService).
        Raises ``VaultSoapError`` on fault.
        """
        body = f"""
          <GetAllLifeCycleDefinitions xmlns="{_CONNECTIVITY_NS}" />
        """
        return self._post(
            "LifeCycleService",
            f"{_CONNECTIVITY_NS}/GetAllLifeCycleDefinitions",
            body,
            category="Connectivity",
        )

    # ----- ItemService: item lifecycle ----------------------------------

    def update_item_lifecycle_states(
        self,
        item_revision_ids: Sequence[int | str],
        to_state_id: int | str,
        comment: str = "Released via release workflow",
    ) -> str:
        """Promote one or more item revisions to the given lifecycle state.

        ``item_revision_ids`` are item-version IDs (REST calls them that —
        SOAP refers to them as "RevisionIds" in the WSDL). Returns the raw
        SOAP response body. Raises ``VaultSoapError`` on fault.
        """
        if not item_revision_ids:
            raise ValueError("item_revision_ids must not be empty")

        ids = list(item_revision_ids)
        ids_xml = _ints_xml("long", ids)
        states_xml = _ints_xml("long", [to_state_id] * len(ids))

        body = f"""
          <UpdateItemLifeCycleStates xmlns="{_FILESTORE_NS}">
            <itemRevIds>{ids_xml}</itemRevIds>
            <toStateIds>{states_xml}</toStateIds>
            <comment>{_escape(comment)}</comment>
          </UpdateItemLifeCycleStates>
        """
        return self._post(
            "ItemService",
            f"{_FILESTORE_NS}/UpdateItemLifeCycleStates",
            body,
        )


# ---------------------------------------------------------------------------
# Tiny XML extractor — avoids pulling in lxml/xmltodict for one operation
# ---------------------------------------------------------------------------

def _extract_tag(xml_text: str, tag: str) -> str | None:
    open_tag = f"<{tag}>"
    close_tag = f"</{tag}>"
    i = xml_text.find(open_tag)
    if i < 0:
        return None
    j = xml_text.find(close_tag, i)
    if j < 0:
        return None
    return xml_text[i + len(open_tag): j].strip()


def parse_lifecycle_state_names(xml_text: str) -> list[str]:
    """Pull a sorted, de-duplicated list of state names out of a
    ``GetAllLifeCycleDefinitions`` SOAP response.

    The response shape is:
      ...<LfCycDef ...>
           <States>
             <LfCycState Id=".." Name="Released" .../>
             <LfCycState Id=".." Name="Work in Progress" .../>
             ...
           </States>
         </LfCycDef>...

    We extract every ``Name="..."`` attribute on a ``LfCycState`` tag
    via a tiny regex — keeps us free of an XML parser dependency for
    one short query.
    """
    import re
    names: set[str] = set()
    for m in re.finditer(
        r'<LfCycState\b[^>]*\sName="([^"]+)"', xml_text,
    ):
        n = m.group(1).strip()
        if n:
            names.add(n)
    return sorted(names, key=str.lower)


__all__ = [
    "VaultSoapError",
    "VaultSoapClient",
    "extract_ticket",
    "decode_access_token",  # deprecated, kept for back-compat
    "parse_lifecycle_state_names",
]
