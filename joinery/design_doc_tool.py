"""The agents' ``save_design_doc`` (gateway RPC, every provider alike).

Only the user adds design documents. When they paste one into the chat and ask
Journeyman to save it, the lead stages it, word for word, in the turn's change: the
file in ``design/docs/`` (where every later session reads it, through
``game_brief.design_documents``) and its line in the brief. Nothing lands before the
user accepts the change, and that acceptance is the user adding it.
"""

from __future__ import annotations

from typing import Any

from joinery import setup_imports
from joinery.gateway import ProjectGateway

NEXT = (
    "The document is staged in your change. Call submit_for_review with one short "
    "line naming it; it joins the project when the user accepts, and later sessions "
    "read it with the game brief."
)
MAX_NAME = 120


async def save(gateway: ProjectGateway, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name", "")
    text = params.get("text", "")
    if not (isinstance(name, str) and isinstance(text, str)) or len(name) > MAX_NAME:
        raise ValueError(
            f"name is a short file name (up to {MAX_NAME} characters) and text is "
            "the document the user pasted, word for word."
        )
    if gateway.turn is not None:
        gateway.turn.admit_mutation()
    async with gateway.call_lock:
        tx = gateway.staging()
        tx.note_tool("joinery")
        path, drafted = setup_imports.save_pasted(tx.stage, name, text)
        if gateway.claims.contended():
            gateway.claim_stage(strict=True)
    return {
        "status": "staged",
        "path": path,
        "drafted": drafted or [],
        "facts": [setup_imports.document_message(1, drafted)],
        "next": NEXT,
    }
