"""Single-identifier validation used by the legacy decorator."""

from __future__ import annotations


def validate_single_id(value, p_name, *, apply_alias, AliasID, is_handle_like, alias_ask_payload):
    if isinstance(value, int):
        if not (-(2**63) <= value <= 2**63 - 1):
            return (
                None,
                f"Invalid {p_name}: {value}. ID is out of the valid integer range.",
            )
        return value, None
    # Handle string IDs
    if isinstance(value, str):
        try:
            int_value = int(value)
            if not (-(2**63) <= int_value <= 2**63 - 1):
                return (
                    None,
                    f"Invalid {p_name}: {value}. ID is out of the valid integer range.",
                )
            return int_value, None
        except ValueError:
            # Saved aliases are free text ("андрей бекендер"), so they must
            # be resolved here: this decorator runs before the tool body
            # ever reaches resolve_entity.
            resolved = apply_alias(value)
            if isinstance(resolved, int):
                # Keep the wording: if the mapping turns out to be
                # stale, the resolver must name it, not the bare id.
                return AliasID(resolved, value), None
            if is_handle_like(value):
                return value, None
            # Unknown or ambiguous reference: hand the agent an
            # instruction to ask the user instead of a dead end.
            return None, alias_ask_payload(value)
    # Handle other invalid types
    return (
        None,
        f"Invalid {p_name}: {value}. Type must be an integer or a string.",
    )
