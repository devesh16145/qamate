"""Content-free character accounting, not provider token or billing estimates."""
import json


def _size(value):
    if value is None:
        return 0
    return len(value) if isinstance(value, str) else len(json.dumps(value, default=str, ensure_ascii=False))


def context_metrics(messages, parameters):
    buckets = {}
    for message in messages:
        for part in getattr(message, "parts", []):
            kind = getattr(part, "part_kind", "unknown")
            buckets[kind] = buckets.get(kind, 0) + _size(getattr(part, "content", None)) + _size(getattr(part, "args", None))
    tools = {tool.name: _size(tool.description) + _size(tool.parameters_json_schema)
             for tool in parameters.function_tools}
    instructions = sum(_size(p.content) for p in (parameters.instruction_parts or []))
    return {"message_chars_by_kind": buckets, "tool_chars_by_name": tools,
            "instruction_chars": instructions, "total_chars": sum(buckets.values()) + sum(tools.values()) + instructions,
            "measurement": "pre_adapter_characters_not_tokens"}
