#!/usr/bin/env python3
"""
Patch LiteLLM's GoogleGenAI adapter to support Gemini functionResponse turns under role="model"
and prevent trailing empty assistant messages that trigger HTTP 400 from Google Gemini backends.
"""
import sys
from pathlib import Path

target_file = Path("/opt/litellm-venv/lib/python3.12/site-packages/litellm/google_genai/adapters/transformation.py")
if not target_file.exists():
    # If testing outside container, locate via python import
    import litellm.google_genai.adapters.transformation as t
    target_file = Path(t.__file__)

code = target_file.read_text(encoding="utf-8")

old_block = """            elif role == "model":
                # Handle assistant messages with potential function calls
                combined_text = ""
                tool_calls: list[ChatCompletionAssistantToolCall] = []

                for part in parts:
                    if isinstance(part, dict):
                        if "text" in part:
                            combined_text += part["text"]
                        elif "functionCall" in part:
                            # Transform function call to tool call
                            func_call = part["functionCall"]
                            tool_call = ChatCompletionAssistantToolCall(
                                id=f"call_{func_call.get('name', 'unknown')}",
                                type="function",
                                function=ChatCompletionToolCallFunctionChunk(
                                    name=func_call.get("name", ""),
                                    arguments=json.dumps(func_call.get("args", {})),
                                ),
                            )
                            tool_calls.append(tool_call)
                    elif isinstance(part, str):
                        combined_text += part

                # Create assistant message
                if tool_calls:
                    assistant_message = ChatCompletionAssistantMessage(
                        role="assistant",
                        content=combined_text if combined_text else None,
                        tool_calls=tool_calls,
                    )
                else:
                    assistant_message = ChatCompletionAssistantMessage(
                        role="assistant",
                        content=combined_text if combined_text else None,
                    )

                messages.append(assistant_message)"""

new_block = """            elif role in ("model", "function"):
                # Handle assistant messages with potential function calls and tool responses
                combined_text = ""
                tool_calls: list[ChatCompletionAssistantToolCall] = []
                tool_messages: list[ChatCompletionToolMessage] = []

                for part in parts:
                    if isinstance(part, dict):
                        if "text" in part:
                            combined_text += part["text"]
                        elif "functionCall" in part:
                            func_call = part["functionCall"]
                            tool_call = ChatCompletionAssistantToolCall(
                                id=f"call_{func_call.get('name', 'unknown')}",
                                type="function",
                                function=ChatCompletionToolCallFunctionChunk(
                                    name=func_call.get("name", ""),
                                    arguments=json.dumps(func_call.get("args", {})),
                                ),
                            )
                            tool_calls.append(tool_call)
                        elif "functionResponse" in part:
                            func_response = part["functionResponse"]
                            tool_message = ChatCompletionToolMessage(
                                role="tool",
                                tool_call_id=f"call_{func_response.get('name', 'unknown')}",
                                content=json.dumps(func_response.get("response", {})),
                            )
                            tool_messages.append(tool_message)
                    elif isinstance(part, str):
                        combined_text += part

                if tool_calls:
                    messages.append(ChatCompletionAssistantMessage(
                        role="assistant",
                        content=combined_text if combined_text else None,
                        tool_calls=tool_calls,
                    ))
                elif combined_text and role == "model":
                    messages.append(ChatCompletionAssistantMessage(
                        role="assistant",
                        content=combined_text,
                    ))

                if tool_messages:
                    messages.extend(tool_messages)"""

if old_block in code:
    code = code.replace(old_block, new_block, 1)
    target_file.write_text(code, encoding="utf-8")
    print(f"Successfully patched {target_file}")
elif new_block in code:
    print(f"Already patched: {target_file}")
else:
    print(f"Error: Target block not found in {target_file}", file=sys.stderr)
    sys.exit(1)
